from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union
import copy
import json
import re

@dataclass
class Violation:
    code: str
    message: str
    path: Optional[str] = None
    severity: str = "error"   # "warn" | "error"
    evidence: Optional[str] = None

@dataclass
class GovernResult:
    ok: bool
    fixed_output: Optional[Any] = None
    violations: List[Violation] = field(default_factory=list)
    needs_regen: bool = False
    regen_instruction: Optional[str] = None

def _path_join(base: str, key: Union[str, int]) -> str:
    if isinstance(key, int):
        return f"{base}[{key}]"
    if base:
        return f"{base}.{key}"
    return str(key)

class JsonRepair:
    """
    Best-effort JSON extraction/repair for common LLM failure modes:
    - leading/trailing commentary around JSON
    - code fences
    - trailing commas
    - limited single-quote normalization (keys only)
    - NEW: salvage truncated JSON arrays by keeping only fully-parsed items and closing the array
    """
    JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)

    @staticmethod
    def strip_code_fences(text: str) -> str:
        m = JsonRepair.JSON_BLOCK_RE.search(text)
        if m:
            return m.group(1).strip()
        return text.strip()

    @staticmethod
    def extract_first_json(text: str) -> Optional[str]:
        """
        Extract first top-level JSON array/object from text by bracket balancing.
        If the JSON is truncated and never closes, returns None.
        """
        s = text
        start = None
        for i, ch in enumerate(s):
            if ch in "[{":
                start = i
                break
        if start is None:
            return None

        open_ch = s[start]
        close_ch = "]" if open_ch == "[" else "}"
        depth = 0
        for j in range(start, len(s)):
            if s[j] == open_ch:
                depth += 1
            elif s[j] == close_ch:
                depth -= 1
                if depth == 0:
                    return s[start : j + 1]
        return None

    @staticmethod
    def remove_trailing_commas(text: str) -> str:
        return re.sub(r",\s*([}\]])", r"\1", text)

    @staticmethod
    def try_parse(text: str) -> Tuple[Optional[Any], Optional[str]]:
        try:
            return json.loads(text), None
        except Exception as e:
            return None, str(e)

    @staticmethod
    def _skip_ws(s: str, i: int) -> int:
        n = len(s)
        while i < n and s[i] in " \t\r\n":
            i += 1
        return i

    @staticmethod
    def salvage_truncated_array(text: str) -> Optional[str]:
        """
        If text contains a JSON array that is truncated (missing the closing ']'),
        salvage it by:
          - locating the first '['
          - decoding array elements sequentially using JSONDecoder.raw_decode
          - keeping only fully decoded elements
          - rebuilding a valid JSON array: '[' + ','.join(decoded_elements_as_text) + ']'

        Returns the repaired JSON string, or None if not salvageable.
        """
        s = text

        start = s.find("[")
        if start < 0:
            return None

        dec = json.JSONDecoder()
        i = start + 1
        n = len(s)

        parts: List[str] = []
        last_good_end: Optional[int] = None

        while True:
            i = JsonRepair._skip_ws(s, i)
            if i >= n:
                break

            if s[i] == "]":
                last_good_end = i + 1
                break

            if s[i] == ",":
                i += 1
                continue

            try:
                _, end = dec.raw_decode(s, i)  # parses a JSON value starting at i
                parts.append(s[i:end])
                last_good_end = end
                i = end
            except Exception:
                break

        if not parts:
            return None

        repaired = "[" + ",".join(parts) + "]"
        repaired = JsonRepair.remove_trailing_commas(repaired)

        return repaired

    @staticmethod
    def repair_and_parse(raw: str) -> Tuple[Optional[Any], List[str]]:
        errors: List[str] = []
        s = JsonRepair.strip_code_fences(raw)

        candidate = JsonRepair.extract_first_json(s) or s

        obj, err = JsonRepair.try_parse(candidate)
        if obj is not None:
            return obj, errors
        errors.append(f"json.loads failed: {err}")

        c2 = JsonRepair.remove_trailing_commas(candidate)
        obj, err = JsonRepair.try_parse(c2)
        if obj is not None:
            return obj, errors
        errors.append(f"after trailing comma removal failed: {err}")

        c3 = re.sub(r"(?P<pre>[\{\s,])'(?P<k>[^']+?)'\s*:", r'\g<pre>"\g<k>":', c2)
        obj, err = JsonRepair.try_parse(c3)
        if obj is not None:
            return obj, errors
        errors.append(f"after key quote repair failed: {err}")

        if "[" in c3:
            salvaged = JsonRepair.salvage_truncated_array(c3)
            if salvaged is not None:
                obj, err = JsonRepair.try_parse(salvaged)
                if obj is not None:
                    errors.append("salvaged truncated JSON array by keeping fully parsed elements and closing ']'.")
                    return obj, errors
                errors.append(f"after truncated array salvage failed: {err}")

        return None, errors

class TraceStripper:
    """
    Placeholder hook: remove prompt-injected 'action trace' content that gets echoed in LLM output.

    You said you'll specify the exact pattern/rules later.
    For now, this is a no-op that provides a stable extension point.
    """

    @staticmethod
    def strip_action_trace_echo(raw_text: str) -> str:
        return raw_text

@dataclass
class StateSchema:
    state_min: int = 1
    state_max: int = 6
    allowed_decisions: Tuple[str, ...] = ("continue", "prune")

    enable_state_clamp: bool = False

class StateGovernor:
    """
    Enforces StateSchema:
      - Root must be JSON object
      - Required keys: state, decision, rationale
      - state must be int in [state_min, state_max]
      - decision must be in allowed_decisions
      - rationale must be string
    """

    def __init__(
        self,
        schema: StateSchema,
        *,
        enable_autofix: bool = True,
    ):
        self.schema = schema
        self.enable_autofix = enable_autofix

    def govern(self, llm_output: Union[str, Any]) -> GovernResult:
        violations: List[Violation] = []

        obj = llm_output
        if isinstance(llm_output, str):
            cleaned = JsonRepair.strip_code_fences(llm_output)
            parsed, parse_errors = JsonRepair.repair_and_parse(cleaned)

            if parsed is None:
                violations.append(Violation(
                    code="STATE_JSON_PARSE_FAILED",
                    message="StateGovernor could not parse valid JSON.",
                    severity="error",
                    evidence="; ".join(parse_errors[-3:]) if parse_errors else None,
                ))
                return self._regen(violations)

            obj = parsed

        if not isinstance(obj, dict):
            violations.append(Violation(
                code="STATE_ROOT_NOT_OBJECT",
                message="StateGovernor expects a JSON object at the root.",
                severity="error",
            ))
            return self._regen(violations)

        fixed = dict(obj)

        for key in ("state", "decision", "rationale", "search_keywords"):
            if key not in fixed:
                violations.append(Violation(
                    code="STATE_MISSING_KEY",
                    message=f"Missing required key '{key}'.",
                    path=key,
                    severity="error",
                ))

        state_val = fixed.get("state")

        if isinstance(state_val, bool) or state_val is None:
            violations.append(Violation(
                code="STATE_INVALID_TYPE",
                message="state must be an integer.",
                path="state",
                severity="error",
            ))
        elif isinstance(state_val, (int, float)):
            state_int = int(state_val)

            if state_int < self.schema.state_min or state_int > self.schema.state_max:
                if self.enable_autofix and self.schema.enable_state_clamp:
                    clamped = max(self.schema.state_min,
                                  min(self.schema.state_max, state_int))
                    fixed["state"] = clamped
                    violations.append(Violation(
                        code="STATE_CLAMPED",
                        message=f"Clamped state {state_int} -> {clamped}.",
                        path="state",
                        severity="warn",
                    ))
                else:
                    violations.append(Violation(
                        code="STATE_OUT_OF_RANGE",
                        message=f"state must be in range {self.schema.state_min}-{self.schema.state_max}.",
                        path="state",
                        severity="error",
                    ))
            else:
                fixed["state"] = state_int

        elif self.enable_autofix and isinstance(state_val, str) and state_val.strip().isdigit():
            state_int = int(state_val.strip())
            fixed["state"] = state_int
            violations.append(Violation(
                code="STATE_COERCED",
                message="Coerced state string to integer.",
                path="state",
                severity="warn",
            ))
        else:
            violations.append(Violation(
                code="STATE_INVALID_TYPE",
                message="state must be an integer.",
                path="state",
                severity="error",
            ))

        decision = fixed.get("decision")

        if isinstance(decision, str):
            norm = decision.strip().lower()

            if norm not in self.schema.allowed_decisions:
                violations.append(Violation(
                    code="STATE_DECISION_INVALID",
                    message=f"decision must be one of {self.schema.allowed_decisions}.",
                    path="decision",
                    severity="error",
                ))
            else:
                fixed["decision"] = norm
                if norm != decision:
                    violations.append(Violation(
                        code="STATE_DECISION_NORMALIZED",
                        message=f"Normalized decision '{decision}' -> '{norm}'.",
                        path="decision",
                        severity="warn",
                    ))
        else:
            violations.append(Violation(
                code="STATE_DECISION_INVALID",
                message="decision must be a string.",
                path="decision",
                severity="error",
            ))

        rationale = fixed.get("rationale")
        if not isinstance(rationale, str):
            violations.append(Violation(
                code="STATE_RATIONALE_INVALID",
                message="rationale must be a string.",
                path="rationale",
                severity="error",
            ))

        sk = fixed.get("search_keywords")

        if not isinstance(sk, list):
            violations.append(Violation(
                code="STATE_SEARCH_KEYWORDS_INVALID",
                message="search_keywords must be a list.",
                path="search_keywords",
                severity="error",
            ))

        has_error = any(v.severity == "error" for v in violations)
        if has_error:
            return self._regen(violations)

        return GovernResult(
            ok=True,
            fixed_output=fixed,
            violations=violations,
        )

    def _regen(self, violations: List[Violation]) -> GovernResult:
        lines = [
            "StateGovernor rejected the output. Re-output strictly valid JSON ONLY.",
            "Constraints:",
            "- Root MUST be a JSON object.",
            "- Required keys: state, decision, rationale, search_keywords.",
            f"- state MUST be integer {self.schema.state_min}-{self.schema.state_max}.",
            f"- decision MUST be one of {self.schema.allowed_decisions}.",
            "- rationale MUST be a string.",
        ]

        top = violations[:8]
        if top:
            lines.append("Top violations:")
            for v in top:
                loc = f" @ {v.path}" if v.path else ""
                lines.append(f"- {v.code}{loc}: {v.message}")

        return GovernResult(
            ok=False,
            fixed_output=None,
            violations=violations,
            needs_regen=True,
            regen_instruction="\n".join(lines),
        )

@dataclass
class ActionSchema:
    """
    Expected output: JSON ARRAY of items. Each item:
      {
        "action": {
          "action_type": "click|type|scroll",
          "kind": ...,
          "text_hint": ...,
          "icon_hint": ...,
          "bbox": [x1,y1,x2,y2],
          "rationale": ...
        },
        "confidence": 1..10
      }

    Special rule:
      - action_type == "scroll": only requires ("action_type", "rationale").
        kind/text_hint/icon_hint/bbox may be null or absent.
      - action_type in {"click","type"}: requires full set including bbox.
    """

    require_item_keys: Tuple[str, ...] = ("action", "confidence")

    require_action_keys_click_type: Tuple[str, ...] = (
        "action_type", "kind", "text_hint", "icon_hint", "bbox", "rationale"
    )
    require_action_keys_scroll: Tuple[str, ...] = (
        "action_type", "rationale"
    )

    allowed_action_type: Tuple[str, ...] = ("click", "type", "scroll", "navigate", "wait")

    confidence_min: int = 1
    confidence_max: int = 10
    bbox_len: int = 4

    max_actions: int = 50

    enable_disallowed_filter: bool = False,
    disallowed_match_case_sensitive: bool = True

class ActionGovernor:
    """
    Enforces ActionSchema:
      - Parses/repairs JSON (best-effort)
      - Validates required fields
      - Validates action_type enum (with alias normalization)
      - Ensures confidence exists at item root (not inside action)
      - Optionally enforces confidence sorted descending (stable for ties)
    """

    def __init__(
        self,
        schema: ActionSchema,
        *,
        enable_autofix: bool = True,
        enforce_confidence_sort: bool = True
    ):
        self.schema = schema
        self.enable_autofix = enable_autofix
        self.enforce_confidence_sort = enforce_confidence_sort

    def govern(self, llm_output: Union[str, Any], disallowed_actions: Optional[List[Dict[str, Any]]] = None) -> GovernResult:
        violations: List[Violation] = []

        obj = llm_output
        if isinstance(llm_output, str):
            cleaned = TraceStripper.strip_action_trace_echo(llm_output)  # placeholder hook
            parsed, parse_errors = JsonRepair.repair_and_parse(cleaned)
            if parsed is None:
                violations.append(Violation(
                    code="ACTION_JSON_PARSE_FAILED",
                    message="ActionGovernor could not parse valid JSON after best-effort repair.",
                    severity="error",
                    evidence="; ".join(parse_errors[-3:]) if parse_errors else None,
                ))
                return self._regen(violations)
            obj = parsed

        if not isinstance(obj, list):
            if self.enable_autofix and isinstance(obj, dict):
                obj = [obj]
                violations.append(Violation(
                    code="ACTION_ROOT_OBJECT_WRAPPED",
                    message="Root was a JSON object (not array); auto-wrapped into a single-element array.",
                    severity="warn",
                ))
            else:
                violations.append(Violation(
                    code="ACTION_ROOT_NOT_ARRAY",
                    message="ActionGovernor expects a JSON array at the root.",
                    severity="error",
                ))
                return self._regen(violations)

        fixed: List[Any] = copy.deepcopy(obj)

        if len(fixed) > self.schema.max_actions:
            if self.enable_autofix:
                fixed = fixed[: self.schema.max_actions]
                violations.append(Violation(
                    code="ACTION_TOO_MANY_ITEMS_TRUNCATED",
                    message=f"Truncated action candidates to max_actions={self.schema.max_actions}.",
                    severity="warn",
                ))
            else:
                violations.append(Violation(
                    code="ACTION_TOO_MANY_ITEMS",
                    message=f"Action candidates exceed max_actions={self.schema.max_actions}.",
                    severity="error",
                ))
                return self._regen(violations)

        if self.enable_autofix:
            _anchor_idx: Optional[int] = None
            _anchor_val: Optional[int] = None
            for _i, _item in enumerate(fixed):
                if not isinstance(_item, dict):
                    continue
                if (
                    "confidence" not in _item
                    and isinstance(_item.get("action"), dict)
                    and "confidence" in _item["action"]
                ):
                    _item["confidence"] = _item["action"].pop("confidence")
                if "confidence" not in _item:
                    if _anchor_idx is not None:
                        _assigned = max(1, _anchor_val + (_anchor_idx - _i))
                        _anchor_label = f"index {_anchor_idx} val {_anchor_val}"
                    else:
                        _assigned = max(1, 9 - _i)
                        _anchor_label = "none, fallback 9-i"
                    _item["confidence"] = _assigned
                    violations.append(Violation(
                        code="ACTION_CONFIDENCE_ASSIGNED",
                        message=f"Missing confidence; assigned {_assigned} (index {_i}, anchor={_anchor_label}).",
                        path=f"[{_i}].confidence",
                        severity="warn",
                    ))
                else:
                    _anchor_idx = _i
                    _anchor_val = _item["confidence"]

        fixed_items, v_item = self._validate_and_fix_items(fixed)
        violations.extend(v_item)

        if self.schema.enable_disallowed_filter and disallowed_actions:
            fixed_items, v_dis = self._remove_disallowed_click_text_hint(fixed_items, disallowed_actions)
            violations.extend(v_dis)

        if self.enforce_confidence_sort:
            fixed_items, v_sort = self._enforce_confidence_sort(fixed_items)
            violations.extend(v_sort)

        has_error = any(v.severity == "error" for v in violations)
        if has_error:
            return self._regen(violations)

        return GovernResult(ok=True, fixed_output=fixed_items, violations=violations)

    def _normalize_action_type(self, at: Any) -> Any:
        if not isinstance(at, str):
            return at
        norm = at.strip().lower()

        aliases = {
            "scroll_down": "scroll",
            "scrolldown": "scroll",
            "scrollup": "scroll",
            "scroll_up": "scroll",
            "scroll-down": "scroll",
            "scroll-up": "scroll",
        }
        return aliases.get(norm, norm)

    def _validate_and_fix_items(self, arr: List[Any]) -> Tuple[List[Dict[str, Any]], List[Violation]]:
        violations: List[Violation] = []
        out: List[Dict[str, Any]] = []

        for i, item in enumerate(arr):
            p = f"[{i}]"

            if not isinstance(item, dict):
                violations.append(Violation(
                    code="ACTION_ITEM_NOT_OBJECT",
                    message="Each action candidate must be an object.",
                    path=p,
                    severity="error",
                    evidence=str(item)[:200],
                ))
                continue

            fixed_item = copy.deepcopy(item)
            drop_item = False

            ACTION_FIELD_KEYS = {
                "action_type", "kind", "text_hint", "icon_hint", "bbox", "rationale"
            }
            if (
                self.enable_autofix
                and "action" not in fixed_item
                and any(k in fixed_item for k in ACTION_FIELD_KEYS)
            ):
                extracted = {k: fixed_item.pop(k) for k in ACTION_FIELD_KEYS if k in fixed_item}
                fixed_item["action"] = extracted
                violations.append(Violation(
                    code="ACTION_FLAT_ITEM_WRAPPED",
                    message="Flat item (action fields at root) auto-wrapped into {'action': {...}, 'confidence': ...} structure.",
                    path=p,
                    severity="warn",
                ))

            if (
                self.enable_autofix
                and "confidence" not in fixed_item
                and isinstance(fixed_item.get("action"), dict)
                and "confidence" in fixed_item["action"]
            ):
                fixed_item["confidence"] = fixed_item["action"].pop("confidence")
                violations.append(Violation(
                    code="ACTION_CONFIDENCE_NESTED_MOVED",
                    message="Moved confidence from action body to item root.",
                    path=_path_join(p, "action.confidence"),
                    severity="warn",
                ))

            for k in self.schema.require_item_keys:
                if k not in fixed_item:
                    if k == "confidence" and self.enable_autofix:
                        assigned = max(1, 9 - i)
                        fixed_item["confidence"] = assigned
                        violations.append(Violation(
                            code="ACTION_CONFIDENCE_ASSIGNED",
                            message=f"Missing confidence; assigned {assigned} based on output order (index {i}).",
                            path=_path_join(p, k),
                            severity="warn",
                        ))
                    else:
                        violations.append(Violation(
                            code="ACTION_MISSING_ITEM_KEY",
                            message=f"Missing required item key '{k}'.",
                            path=_path_join(p, k),
                            severity="error",
                        ))

            action = fixed_item.get("action")
            if not isinstance(action, dict):
                violations.append(Violation(
                    code="ACTION_FIELD_NOT_OBJECT",
                    message="Item field 'action' must be an object.",
                    path=_path_join(p, "action"),
                    severity="error",
                ))
                continue

            if "confidence" in action:
                if self.enable_autofix and "confidence" not in fixed_item:
                    fixed_item["confidence"] = action.pop("confidence")
                    violations.append(Violation(
                        code="ACTION_CONFIDENCE_NESTED_MOVED",
                        message="Moved confidence from action body to item root.",
                        path=_path_join(p, "action.confidence"),
                        severity="warn",
                    ))
                elif self.enable_autofix:
                    action.pop("confidence")
                    violations.append(Violation(
                        code="ACTION_CONFIDENCE_NESTED_REMOVED",
                        message="Removed duplicate confidence from action body (kept item root value).",
                        path=_path_join(p, "action.confidence"),
                        severity="warn",
                    ))
                else:
                    violations.append(Violation(
                        code="ACTION_CONFIDENCE_NESTED",
                        message="confidence must be at the item root, not inside action.",
                        path=_path_join(p, "action.confidence"),
                        severity="error",
                    ))

            if "action_type" in action:
                old = action.get("action_type")
                norm = self._normalize_action_type(old)
                if norm != old and self.enable_autofix:
                    action["action_type"] = norm
                    violations.append(Violation(
                        code="ACTION_ACTION_TYPE_NORMALIZED",
                        message=f"Normalized action_type '{old}' -> '{norm}'.",
                        path=_path_join(p, "action.action_type"),
                        severity="warn",
                    ))

            kind = action.get("kind")
            at = action.get("action_type")

            if kind == "input" and at != "type":
                if self.enable_autofix:
                    old = at
                    action["action_type"] = "type"
                    violations.append(Violation(
                        code="ACTION_INPUT_KIND_FORCED_TYPE",
                        message=f"Forced action_type '{old}' -> 'type' because kind is 'input'.",
                        path=_path_join(p, "action.action_type"),
                        severity="warn",
                    ))
                    at = "type"
                else:
                    violations.append(Violation(
                        code="ACTION_INPUT_KIND_REQUIRES_TYPE",
                        message="When kind is 'input', action_type must be 'type'.",
                        path=_path_join(p, "action.action_type"),
                        severity="error",
                    ))
            if kind == 'href':
                if self.enable_autofix:
                    old = at
                    action["action_type"] = "navigate"
                    violations.append(Violation(
                        code="ACTION_HREF_KIND_FORCED_NAVIGATE",
                        message=f"Forced action_type '{old}' -> 'navigate' because kind is 'href'.",
                        path=_path_join(p, "action.action_type"),
                        severity="warn",
                    ))
                    at = "navigate"
                else:
                    violations.append(Violation(
                        code="ACTION_HREF_KIND_REQUIRES_NAVIGATE",
                        message="When kind is 'href', action_type must be 'navigate'.",
                        path=_path_join(p, "action.action_type"),
                        severity="error",
                    ))

            if at == "navigate":
                if action.get("kind") != "href":
                    if self.enable_autofix:
                        action["kind"] = "href"
                        violations.append(Violation(
                            code="ACTION_NAVIGATE_KIND_FIXED",
                            message="Forced kind='href' for navigate action.",
                            path=_path_join(p, "action.kind"),
                            severity="warn",
                        ))
                    else:
                        violations.append(Violation(
                            code="ACTION_NAVIGATE_KIND_INVALID",
                            message="navigate action must have kind='href'.",
                            path=_path_join(p, "action.kind"),
                            severity="error",
                        ))

                _href_re = re.compile(
                    r'^(?:https?://|//|/|\.{1,2}/|[a-zA-Z][a-zA-Z0-9+\-.]*://|#|\?)',
                    re.IGNORECASE,
                )
                th = action.get("text_hint")
                if not isinstance(th, str):
                    violations.append(Violation(
                        code="ACTION_NAVIGATE_TEXT_HINT_INVALID",
                        message="navigate action must have text_hint as href string.",
                        path=_path_join(p, "action.text_hint"),
                        severity="error",
                    ))
                elif not _href_re.match(th.strip()):
                    violations.append(Violation(
                        code="ACTION_NAVIGATE_TEXT_HINT_NOT_HREF_DROPPED",
                        message=(
                            f"navigate action dropped: text_hint '{th[:80]}' is not a valid href. "
                            "Expected a URL or path (e.g. '/settings', 'https://...')."
                        ),
                        path=_path_join(p, "action.text_hint"),
                        severity="warn",
                    ))
                    drop_item = True

                if action.get("icon_hint") is not None:
                    if self.enable_autofix:
                        action["icon_hint"] = None
                        violations.append(Violation(
                            code="ACTION_NAVIGATE_ICON_NULL",
                            message="Forced icon_hint=None for navigate action.",
                            path=_path_join(p, "action.icon_hint"),
                            severity="warn",
                        ))
                    else:
                        violations.append(Violation(
                            code="ACTION_NAVIGATE_ICON_INVALID",
                            message="navigate action must have icon_hint=null.",
                            path=_path_join(p, "action.icon_hint"),
                            severity="error",
                        ))

                if action.get("bbox") is not None:
                    if self.enable_autofix:
                        action["bbox"] = None
                        violations.append(Violation(
                            code="ACTION_NAVIGATE_BBOX_NULL",
                            message="Forced bbox=None for navigate action.",
                            path=_path_join(p, "action.bbox"),
                            severity="warn",
                        ))
                    else:
                        violations.append(Violation(
                            code="ACTION_NAVIGATE_BBOX_INVALID",
                            message="navigate action must have bbox=null.",
                            path=_path_join(p, "action.bbox"),
                            severity="error",
                        ))

            required_keys = (
                self.schema.require_action_keys_scroll
                if at == "scroll"
                else self.schema.require_action_keys_click_type
            )

            for k in required_keys:
                if k not in action:
                    if self.enable_autofix and k in ("text_hint", "icon_hint", "rationale"):
                        action[k] = None if k != "rationale" else ""
                        violations.append(Violation(
                            code="ACTION_MISSING_ACTION_KEY_AUTOFILLED",
                            message=f"Missing action key '{k}' auto-filled.",
                            path=_path_join(p, f"action.{k}"),
                            severity="warn",
                        ))
                    else:
                        violations.append(Violation(
                            code="ACTION_MISSING_ACTION_KEY",
                            message=f"Missing required action key '{k}'.",
                            path=_path_join(p, f"action.{k}"),
                            severity="error",
                        ))

            if at not in self.schema.allowed_action_type:
                violations.append(Violation(
                    code="ACTION_ACTION_TYPE_INVALID",
                    message=f"Invalid action_type '{at}'. Allowed: {self.schema.allowed_action_type}.",
                    path=_path_join(p, "action.action_type"),
                    severity="error",
                ))

            if at in ("click", "type"):
                self._validate_bbox_click_type(action, p, violations)

            self._validate_confidence(fixed_item, p, violations)

            fixed_item["action"] = action
            if not drop_item:
                out.append({"action": action, "confidence": fixed_item.get("confidence")})

        return out, violations

    def _validate_bbox_click_type(self, action: Dict[str, Any], p: str, violations: List[Violation]) -> None:
        bbox = action.get("bbox")

        if not (isinstance(bbox, list) and len(bbox) == self.schema.bbox_len):
            violations.append(Violation(
                code="ACTION_BBOX_INVALID",
                message=f"bbox must be a list of length {self.schema.bbox_len}.",
                path=_path_join(p, "action.bbox"),
                severity="error",
                evidence=str(bbox)[:200],
            ))
            return

        coords: List[int] = []
        ok = True

        for j, v in enumerate(bbox):
            if isinstance(v, bool):
                ok = False
                violations.append(Violation(
                    code="ACTION_BBOX_NON_NUMERIC",
                    message="bbox coordinates must be numeric (bool is not allowed).",
                    path=_path_join(p, f"action.bbox[{j}]"),
                    severity="error",
                    evidence=str(v),
                ))
                break

            if isinstance(v, (int, float)):
                coords.append(int(v))
            elif self.enable_autofix and isinstance(v, str) and v.strip().lstrip("-").isdigit():
                coords.append(int(v.strip()))
                violations.append(Violation(
                    code="ACTION_BBOX_COERCED_INT",
                    message="Coerced bbox coordinate to int.",
                    path=_path_join(p, f"action.bbox[{j}]"),
                    severity="warn",
                ))
            else:
                ok = False
                violations.append(Violation(
                    code="ACTION_BBOX_NON_NUMERIC",
                    message="bbox coordinates must be numeric.",
                    path=_path_join(p, f"action.bbox[{j}]"),
                    severity="error",
                    evidence=str(v)[:100],
                ))
                break

        if not ok:
            return

        x1, y1, x2, y2 = coords
        nx1, nx2 = (x1, x2) if x1 <= x2 else (x2, x1)
        ny1, ny2 = (y1, y2) if y1 <= y2 else (y2, y1)

        if self.enable_autofix and (nx1, ny1, nx2, ny2) != (x1, y1, x2, y2):
            action["bbox"] = [nx1, ny1, nx2, ny2]
            violations.append(Violation(
                code="ACTION_BBOX_REORDERED",
                message="Reordered bbox to ensure x1<=x2 and y1<=y2.",
                path=_path_join(p, "action.bbox"),
                severity="warn",
            ))
        else:
            action["bbox"] = [x1, y1, x2, y2]

    def _validate_confidence(self, item: Dict[str, Any], p: str, violations: List[Violation]) -> None:
        c = item.get("confidence")

        if isinstance(c, bool) or c is None:
            violations.append(Violation(
                code="ACTION_CONFIDENCE_INVALID",
                message="confidence must be an integer.",
                path=_path_join(p, "confidence"),
                severity="error",
                evidence=str(c),
            ))
            return

        if isinstance(c, (int, float)):
            ci = int(c)
            if ci < self.schema.confidence_min or ci > self.schema.confidence_max:
                if self.enable_autofix:
                    clamped = max(self.schema.confidence_min, min(self.schema.confidence_max, ci))
                    item["confidence"] = clamped
                    violations.append(Violation(
                        code="ACTION_CONFIDENCE_CLAMPED",
                        message=f"Clamped confidence {ci} -> {clamped}.",
                        path=_path_join(p, "confidence"),
                        severity="warn",
                    ))
                else:
                    violations.append(Violation(
                        code="ACTION_CONFIDENCE_OUT_OF_RANGE",
                        message=f"confidence must be in range {self.schema.confidence_min}-{self.schema.confidence_max}.",
                        path=_path_join(p, "confidence"),
                        severity="error",
                    ))
            else:
                item["confidence"] = ci
            return

        if self.enable_autofix and isinstance(c, str) and c.strip().isdigit():
            ci = int(c.strip())
            clamped = max(self.schema.confidence_min, min(self.schema.confidence_max, ci))
            item["confidence"] = clamped
            violations.append(Violation(
                code="ACTION_CONFIDENCE_COERCED",
                message="Coerced confidence string to integer (and clamped if needed).",
                path=_path_join(p, "confidence"),
                severity="warn",
            ))
            return

        violations.append(Violation(
            code="ACTION_CONFIDENCE_INVALID",
            message="confidence must be an integer.",
            path=_path_join(p, "confidence"),
            severity="error",
            evidence=str(c)[:200],
        ))

    def _remove_disallowed_click_text_hint(
        self,
        arr: List[Dict[str, Any]],
        disallowed_actions: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], List[Violation]]:
        """
        Autofix policy (A):
        - Remove candidates that EXACTLY match disallowed actions
        - Apply ONLY when candidate.action.action_type == "click" AND candidate.action.text_hint is not None
        - Exact match key: (action_type, kind, text_hint) where action_type fixed to "click"
        """

        violations: List[Violation] = []

        disallowed_keys = set()
        for d in disallowed_actions:
            if not isinstance(d, dict):
                continue
            if d.get("action_type") != "click":
                continue
            if d.get("text_hint") is None:
                continue
            kind = d.get("kind")
            text = d.get("text_hint")

            if isinstance(kind, str) and isinstance(text, str):
                if self.schema.disallowed_match_case_sensitive:
                    disallowed_keys.add((kind, text.strip()))
                else:
                    disallowed_keys.add((kind.lower().strip(), text.lower().strip()))

        if not disallowed_keys:
            return arr, violations

        kept: List[Dict[str, Any]] = []
        removed = 0

        for i, item in enumerate(arr):
            action = item.get("action")
            if not isinstance(action, dict):
                kept.append(item)
                continue

            at = action.get("action_type")
            if at != "click":
                kept.append(item)
                continue

            text_hint = action.get("text_hint")
            kind = action.get("kind")

            if not (isinstance(kind, str) and isinstance(text_hint, str)):
                kept.append(item)
                continue

            if self.schema.disallowed_match_case_sensitive:
                key = (kind, text_hint.strip())
            else:
                key = (kind.lower().strip(), text_hint.lower().strip())

            if key in disallowed_keys:
                removed += 1
                violations.append(Violation(
                    code="ACTION_DISALLOWED_REMOVED",
                    message="Removed a disallowed click action (exact match on kind + text_hint).",
                    path=f"[{i}].action",
                    severity="warn",
                    evidence=f"click/{kind}/{text_hint}",
                ))
                continue

            kept.append(item)

        if removed > 0 and len(kept) == 0:
            violations.append(Violation(
                code="ACTION_ALL_CANDIDATES_DISALLOWED",
                message="All candidates were disallowed; must generate new distinct actions.",
                severity="warn",
            ))

        return kept, violations

    def _enforce_confidence_sort(self, arr: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Violation]]:
        violations: List[Violation] = []

        confs: List[int] = []
        for item in arr:
            c = item.get("confidence")
            if not isinstance(c, int):
                return arr, violations
            confs.append(c)

        nonincreasing = all(confs[i] >= confs[i + 1] for i in range(len(confs) - 1))
        if nonincreasing:
            return arr, violations

        if self.enable_autofix:
            fixed = sorted(arr, key=lambda x: x["confidence"], reverse=True)  # stable
            violations.append(Violation(
                code="ACTION_CONFIDENCE_REORDERED",
                message="Reordered action candidates by confidence descending (stable for ties).",
                severity="warn",
            ))
            return fixed, violations

        violations.append(Violation(
            code="ACTION_CONFIDENCE_NOT_SORTED",
            message="Action candidates must be sorted by confidence descending.",
            severity="error",
        ))
        return arr, violations

    def _regen(self, violations: List[Violation]) -> GovernResult:
        lines = [
            "ActionGovernor rejected the output. Re-output strictly valid JSON ONLY.",
            "Constraints:",
            "- Root MUST be a JSON array of items.",
            "- Each item MUST have keys: action, confidence.",
            "- action.action_type MUST be one of: ('click','type','scroll').",
            "- If action_type is 'scroll': action MUST include (action_type, rationale). Other fields may be null/absent.",
            "- If action_type is 'click' or 'type': action MUST include (action_type, kind, text_hint, icon_hint, bbox, rationale).",
            f"- confidence MUST be integer {self.schema.confidence_min}-{self.schema.confidence_max}.",
            "- bbox MUST be [x1,y1,x2,y2] with numeric ints (click/type only).",
        ]
        top = violations[:8]
        if top:
            lines.append("Top violations:")
            for v in top:
                loc = f" @ {v.path}" if v.path else ""
                lines.append(f"- {v.code}{loc}: {v.message}")

        return GovernResult(
            ok=False,
            fixed_output=None,
            violations=violations,
            needs_regen=True,
            regen_instruction="\n".join(lines),
        )

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

@dataclass
class ElementSchema:
    """
    Expected output (per SelectElement prompt):
    {
      "selected": { "index": int, "rationale": str, "relevance": int (1..10) },
      "value": <literal | symbolic_token | null>
    }

    Notes:
    - If action_type != "type": value MUST be null (optional enforcement; see below).
    - If action_type == "type": value MUST be non-null (optional enforcement; see below).
      (We can enforce this if you pass the proposed action into govern(); current signature doesn't.)
    """
    relevance_min: int = 1
    relevance_max: int = 10

    enforce_value_null_when_not_type: bool = False
    enforce_value_nonnull_when_type: bool = False

    allowed_symbolic_tokens: Optional[List[str]] = field(default_factory=lambda: [
        "email_retriever",
        "password_retriever",
        "2FA_retriever",
        "CAPTCHA_solver",
        "human_intervention",
    ])

class ElementGovernor:
    """
    Enforces ElementSchema for single element selection output.
    Matches SelectElement prompt:
      - Root object with keys: selected, value
      - selected has: index, rationale, relevance
      - value is null or string/number/bool/object/array (JSON literal) depending on policy
    """

    def __init__(self, schema: ElementSchema, *, enable_autofix: bool = True):
        self.schema = schema
        self.enable_autofix = enable_autofix

    def govern(self, llm_output: Union[str, Any], proposed_action: Optional[Dict[str, Any]] = None):
        """
        proposed_action (optional):
          If provided and schema.enforce_value_* is enabled, we enforce:
          - action_type != "type" => value must be null
          - action_type == "type" => value must be non-null
        Returns: GovernResult
        """
        violations: List[Violation] = []

        obj = llm_output
        if isinstance(llm_output, str):
            cleaned = TraceStripper.strip_action_trace_echo(llm_output)  # placeholder hook
            parsed, parse_errors = JsonRepair.repair_and_parse(cleaned)
            if parsed is None:
                violations.append(Violation(
                    code="ELEMENT_JSON_PARSE_FAILED",
                    message="ElementGovernor could not parse valid JSON after best-effort repair.",
                    severity="error",
                    evidence="; ".join(parse_errors[-3:]) if parse_errors else None,
                ))
                return self._regen(violations)
            obj = parsed

        if not isinstance(obj, dict):
            violations.append(Violation(
                code="ELEMENT_ROOT_NOT_OBJECT",
                message="ElementGovernor expects a JSON object at the root.",
                severity="error",
            ))
            return self._regen(violations)

        for k in ("selected", "value"):
            if k not in obj:
                violations.append(Violation(
                    code="ELEMENT_MISSING_ROOT_KEY",
                    message=f"Missing required root key '{k}'.",
                    path=_path_join("", k),
                    severity="error",
                ))

        selected = obj.get("selected")
        if not isinstance(selected, dict):
            violations.append(Violation(
                code="ELEMENT_SELECTED_NOT_OBJECT",
                message="'selected' must be an object.",
                path="selected",
                severity="error",
            ))
            return self._regen(violations)

        for k in ("index", "rationale", "relevance"):
            if k not in selected:
                violations.append(Violation(
                    code="ELEMENT_MISSING_SELECTED_KEY",
                    message=f"Missing required selected key '{k}'.",
                    path=_path_join("selected", k),
                    severity="error",
                ))

        if "index" in selected and not isinstance(selected["index"], int):
            violations.append(Violation(
                code="ELEMENT_INDEX_INVALID",
                message="selected.index must be an integer.",
                path="selected.index",
                severity="error",
                evidence=str(selected.get("index"))[:200],
            ))

        if "rationale" in selected and not isinstance(selected["rationale"], str):
            violations.append(Violation(
                code="ELEMENT_RATIONALE_INVALID",
                message="selected.rationale must be a string.",
                path="selected.rationale",
                severity="error",
                evidence=str(selected.get("rationale"))[:200],
            ))

        if "relevance" in selected:
            r = selected.get("relevance")
            if not isinstance(r, int):
                violations.append(Violation(
                    code="ELEMENT_RELEVANCE_INVALID",
                    message="selected.relevance must be an integer.",
                    path="selected.relevance",
                    severity="error",
                    evidence=str(r)[:200],
                ))
            else:
                if r < self.schema.relevance_min or r > self.schema.relevance_max:
                    if self.enable_autofix:
                        clamped = max(self.schema.relevance_min, min(self.schema.relevance_max, r))
                        selected["relevance"] = clamped
                        violations.append(Violation(
                            code="ELEMENT_RELEVANCE_CLAMPED",
                            message=f"Clamped relevance {r} -> {clamped}.",
                            path="selected.relevance",
                            severity="warn",
                        ))
                    else:
                        violations.append(Violation(
                            code="ELEMENT_RELEVANCE_OUT_OF_RANGE",
                            message=f"selected.relevance must be in range {self.schema.relevance_min}-{self.schema.relevance_max}.",
                            path="selected.relevance",
                            severity="error",
                        ))

        value = obj.get("value", None)

        if isinstance(value, str) and self.schema.allowed_symbolic_tokens:
            if value in self.schema.allowed_symbolic_tokens:
                pass
            else:
                violations.append(Violation(
                    code="ELEMENT_VALUE_UNKNOWN_STRING",
                    message="value is a string not in known symbolic tokens; treating as a literal string.",
                    path="value",
                    severity="warn",
                    evidence=value[:200],
                ))

        if proposed_action and isinstance(proposed_action, dict):
            at = proposed_action.get("action_type") or proposed_action.get("action", {}).get("action_type")
            if self.schema.enforce_value_null_when_not_type and at != "type":
                if value is not None:
                    if self.enable_autofix:
                        obj["value"] = None
                        violations.append(Violation(
                            code="ELEMENT_VALUE_FORCED_NULL",
                            message="Forced value=null because action_type is not 'type'.",
                            path="value",
                            severity="warn",
                        ))
                    else:
                        violations.append(Violation(
                            code="ELEMENT_VALUE_MUST_BE_NULL",
                            message="value must be null when action_type is not 'type'.",
                            path="value",
                            severity="error",
                        ))
            if self.schema.enforce_value_nonnull_when_type and at == "type":
                if value is None:
                    violations.append(Violation(
                        code="ELEMENT_VALUE_REQUIRED_FOR_TYPE",
                        message="value must be non-null when action_type is 'type'.",
                        path="value",
                        severity="error",
                    ))

        has_error = any(v.severity == "error" for v in violations)
        if has_error:
            return self._regen(violations)

        obj["selected"] = selected
        return GovernResult(ok=True, fixed_output=obj, violations=violations)

    def _regen(self, violations: List[Violation]) -> GovernResult:
        lines = [
            "ElementGovernor rejected the output. Re-output strictly valid JSON ONLY.",
            "Constraints:",
            "- Root MUST be a JSON object with keys: selected, value.",
            "- selected MUST include: index (int), rationale (str), relevance (int).",
            f"- selected.relevance MUST be {self.schema.relevance_min}-{self.schema.relevance_max}.",
            "- value MUST be null unless action_type == 'type'. (If action_type == 'type', value MUST be provided.)",
        ]
        top = violations[:8]
        if top:
            lines.append("Top violations:")
            for v in top:
                loc = f" @ {v.path}" if v.path else ""
                lines.append(f"- {v.code}{loc}: {v.message}")

        return GovernResult(
            ok=False,
            fixed_output=None,
            violations=violations,
            needs_regen=True,
            regen_instruction="\n".join(lines),
        )

@dataclass
class BBoxRefinementSchema:
    """
    Expected output (per BBoxRefinement prompt):
    {
      "match": "yes" | "no",
      "bbox": [x1, y1, x2, y2] | null,
      "observed_element_in_bbox": str
    }

    Rules (from prompt):
    - match == "yes"  => bbox MUST be null
    - match == "no"   => bbox MUST be [x1,y1,x2,y2] or null (corrected coords or null if uncorrectable)
    - observed_element_in_bbox MUST be a non-empty string

    Optional image-bounds clamping:
    - If image_width / image_height are provided, coordinates are clamped to [0, W] x [0, H].
    """
    allowed_match_values: Tuple[str, ...] = ("yes", "no")
    bbox_len: int = 4

    image_width: Optional[int] = None
    image_height: Optional[int] = None

    min_description_len: int = 3

class BBoxRefinementGovernor:
    """
    Enforces BBoxRefinementSchema for BBoxRefinement LLM output.

    Autofix capabilities:
    - Normalize match value (strip + lowercase)
    - match=="yes" with non-null bbox => force bbox=null (warn)
    - match=="no" with null bbox => allowed (model couldn't correct)
    - Reorder bbox coords so x1<=x2, y1<=y2
    - Coerce string numeric coords to int
    - Clamp coords to image bounds (if schema.image_width/height set)
    """

    def __init__(self, schema: BBoxRefinementSchema, *, enable_autofix: bool = True):
        self.schema = schema
        self.enable_autofix = enable_autofix

    def govern(self, llm_output: Union[str, Any]) -> GovernResult:
        violations: List[Violation] = []

        obj = llm_output
        if isinstance(llm_output, str):
            cleaned = TraceStripper.strip_action_trace_echo(llm_output)
            parsed, parse_errors = JsonRepair.repair_and_parse(cleaned)
            if parsed is None:
                violations.append(Violation(
                    code="BBOX_JSON_PARSE_FAILED",
                    message="BBoxRefinementGovernor could not parse valid JSON after best-effort repair.",
                    severity="error",
                    evidence="; ".join(parse_errors[-3:]) if parse_errors else None,
                ))
                return self._regen(violations)
            obj = parsed

        if not isinstance(obj, dict):
            violations.append(Violation(
                code="BBOX_ROOT_NOT_OBJECT",
                message="BBoxRefinementGovernor expects a JSON object at the root.",
                severity="error",
            ))
            return self._regen(violations)

        fixed = copy.deepcopy(obj)

        for k in ("match", "bbox", "observed_element_in_bbox"):
            if k not in fixed:
                violations.append(Violation(
                    code="BBOX_MISSING_KEY",
                    message=f"Missing required key '{k}'.",
                    path=k,
                    severity="error",
                ))

        if any(v.severity == "error" for v in violations):
            return self._regen(violations)

        match_val = fixed.get("match")
        if not isinstance(match_val, str):
            violations.append(Violation(
                code="BBOX_MATCH_INVALID_TYPE",
                message="'match' must be a string ('yes' or 'no').",
                path="match",
                severity="error",
                evidence=str(match_val)[:100],
            ))
            return self._regen(violations)

        norm_match = match_val.strip().lower()
        if norm_match not in self.schema.allowed_match_values:
            violations.append(Violation(
                code="BBOX_MATCH_INVALID_VALUE",
                message=f"'match' must be one of {self.schema.allowed_match_values}.",
                path="match",
                severity="error",
                evidence=match_val[:100],
            ))
            return self._regen(violations)

        if norm_match != match_val and self.enable_autofix:
            violations.append(Violation(
                code="BBOX_MATCH_NORMALIZED",
                message=f"Normalized match '{match_val}' -> '{norm_match}'.",
                path="match",
                severity="warn",
            ))
        fixed["match"] = norm_match

        bbox_val = fixed.get("bbox")

        if norm_match == "yes":
            if bbox_val is not None:
                if self.enable_autofix:
                    fixed["bbox"] = None
                    violations.append(Violation(
                        code="BBOX_FORCED_NULL_ON_YES",
                        message="Forced bbox=null because match='yes'.",
                        path="bbox",
                        severity="warn",
                    ))
                else:
                    violations.append(Violation(
                        code="BBOX_MUST_BE_NULL_ON_YES",
                        message="bbox must be null when match='yes'.",
                        path="bbox",
                        severity="error",
                    ))
        else:
            if bbox_val is not None:
                fixed_bbox, v_bbox = self._validate_and_fix_bbox(bbox_val)
                violations.extend(v_bbox)
                fixed["bbox"] = fixed_bbox

        desc = fixed.get("observed_element_in_bbox")
        if not isinstance(desc, str):
            violations.append(Violation(
                code="BBOX_DESC_INVALID_TYPE",
                message="'observed_element_in_bbox' must be a string.",
                path="observed_element_in_bbox",
                severity="error",
                evidence=str(desc)[:100],
            ))
        elif len(desc.strip()) < self.schema.min_description_len:
            violations.append(Violation(
                code="BBOX_DESC_TOO_SHORT",
                message=f"'observed_element_in_bbox' must be at least {self.schema.min_description_len} characters.",
                path="observed_element_in_bbox",
                severity="error",
                evidence=repr(desc),
            ))

        has_error = any(v.severity == "error" for v in violations)
        if has_error:
            return self._regen(violations)

        return GovernResult(ok=True, fixed_output=fixed, violations=violations)

    def _validate_and_fix_bbox(
        self, bbox: Any
    ) -> Tuple[Optional[Any], List[Violation]]:
        violations: List[Violation] = []

        if not isinstance(bbox, list) or len(bbox) != self.schema.bbox_len:
            violations.append(Violation(
                code="BBOX_COORDS_INVALID_SHAPE",
                message=f"bbox must be a list of {self.schema.bbox_len} numeric values.",
                path="bbox",
                severity="error",
                evidence=str(bbox)[:200],
            ))
            return None, violations

        coords: List[int] = []
        for j, v in enumerate(bbox):
            if isinstance(v, bool):
                violations.append(Violation(
                    code="BBOX_COORD_NON_NUMERIC",
                    message="bbox coordinates must be numeric (bool not allowed).",
                    path=f"bbox[{j}]",
                    severity="error",
                    evidence=str(v),
                ))
                return None, violations
            elif isinstance(v, (int, float)):
                coords.append(int(v))
            elif self.enable_autofix and isinstance(v, str) and v.strip().lstrip("-").isdigit():
                coords.append(int(v.strip()))
                violations.append(Violation(
                    code="BBOX_COORD_COERCED",
                    message="Coerced bbox coordinate string to int.",
                    path=f"bbox[{j}]",
                    severity="warn",
                ))
            else:
                violations.append(Violation(
                    code="BBOX_COORD_NON_NUMERIC",
                    message="bbox coordinates must be numeric.",
                    path=f"bbox[{j}]",
                    severity="error",
                    evidence=str(v)[:100],
                ))
                return None, violations

        x1, y1, x2, y2 = coords

        nx1, nx2 = (x1, x2) if x1 <= x2 else (x2, x1)
        ny1, ny2 = (y1, y2) if y1 <= y2 else (y2, y1)
        if self.enable_autofix and (nx1, ny1, nx2, ny2) != (x1, y1, x2, y2):
            violations.append(Violation(
                code="BBOX_COORDS_REORDERED",
                message="Reordered bbox to ensure x1<=x2 and y1<=y2.",
                path="bbox",
                severity="warn",
            ))
        x1, y1, x2, y2 = nx1, ny1, nx2, ny2

        if self.schema.image_width is not None and self.schema.image_height is not None:
            w, h = self.schema.image_width, self.schema.image_height
            cx1 = max(0, min(w, x1))
            cy1 = max(0, min(h, y1))
            cx2 = max(0, min(w, x2))
            cy2 = max(0, min(h, y2))
            if self.enable_autofix and (cx1, cy1, cx2, cy2) != (x1, y1, x2, y2):
                violations.append(Violation(
                    code="BBOX_COORDS_CLAMPED",
                    message=f"Clamped bbox coords to image bounds ({w}x{h}).",
                    path="bbox",
                    severity="warn",
                ))
            x1, y1, x2, y2 = cx1, cy1, cx2, cy2

        return [x1, y1, x2, y2], violations

    def _regen(self, violations: List[Violation]) -> GovernResult:
        lines = [
            "BBoxRefinementGovernor rejected the output. Re-output strictly valid JSON ONLY.",
            "Constraints:",
            "- Root MUST be a JSON object with keys: match, bbox, observed_element_in_bbox.",
            "- match MUST be 'yes' or 'no'.",
            "- If match='yes': bbox MUST be null.",
            "- If match='no': bbox MUST be corrected [x1,y1,x2,y2] coords or null if uncorrectable.",
            "- observed_element_in_bbox MUST be a non-empty descriptive string (one sentence).",
        ]
        top = violations[:8]
        if top:
            lines.append("Top violations:")
            for v in top:
                loc = f" @ {v.path}" if v.path else ""
                lines.append(f"- {v.code}{loc}: {v.message}")

        return GovernResult(
            ok=False,
            fixed_output=None,
            violations=violations,
            needs_regen=True,
            regen_instruction="\n".join(lines),
        )

@dataclass
class ClickPointSchema:
    """
    Expected output (per ClickPoint prompt):
    {
      "click_point": [x, y]
    }

    Rules:
    - click_point MUST be a list of exactly 2 numeric values [x, y].
    - Both x and y MUST be strictly inside the validated element bbox (NOT on boundary).
    - Both x and y MUST be within image bounds: 0 <= x <= width, 0 <= y <= height.

    Optional bbox / image-bounds enforcement:
    - If validated_bbox is provided ([x1,y1,x2,y2]), the click_point is checked to be
      strictly inside (x1 < x < x2, y1 < y < y2). If outside, autofix centers it.
    - If image_width / image_height are provided, coords are clamped.
    """
    click_point_len: int = 2

    validated_bbox: Optional[List[int]] = None    # [x1, y1, x2, y2] in cropped image space
    image_width: Optional[int] = None
    image_height: Optional[int] = None

class ClickPointGovernor:
    """
    Enforces ClickPointSchema for ClickPoint LLM output.

    Autofix capabilities:
    - Coerce string numeric coords to int/float
    - Clamp to image bounds (if schema.image_width/height set)
    - If click_point is outside validated_bbox: autofix to bbox center (warn)
    """

    def __init__(self, schema: ClickPointSchema, *, enable_autofix: bool = True):
        self.schema = schema
        self.enable_autofix = enable_autofix

    def govern(self, llm_output: Union[str, Any]) -> GovernResult:
        violations: List[Violation] = []

        obj = llm_output
        if isinstance(llm_output, str):
            cleaned = TraceStripper.strip_action_trace_echo(llm_output)
            parsed, parse_errors = JsonRepair.repair_and_parse(cleaned)
            if parsed is None:
                violations.append(Violation(
                    code="CLICK_JSON_PARSE_FAILED",
                    message="ClickPointGovernor could not parse valid JSON after best-effort repair.",
                    severity="error",
                    evidence="; ".join(parse_errors[-3:]) if parse_errors else None,
                ))
                return self._regen(violations)
            obj = parsed

        if not isinstance(obj, dict):
            violations.append(Violation(
                code="CLICK_ROOT_NOT_OBJECT",
                message="ClickPointGovernor expects a JSON object at the root.",
                severity="error",
            ))
            return self._regen(violations)

        fixed = copy.deepcopy(obj)

        if "click_point" not in fixed:
            violations.append(Violation(
                code="CLICK_MISSING_KEY",
                message="Missing required key 'click_point'.",
                path="click_point",
                severity="error",
            ))
            return self._regen(violations)

        cp = fixed.get("click_point")
        if not isinstance(cp, list) or len(cp) != self.schema.click_point_len:
            violations.append(Violation(
                code="CLICK_POINT_INVALID_SHAPE",
                message=f"'click_point' must be a list of {self.schema.click_point_len} numeric values [x, y].",
                path="click_point",
                severity="error",
                evidence=str(cp)[:200],
            ))
            return self._regen(violations)

        coords: List[float] = []
        for j, v in enumerate(cp):
            if isinstance(v, bool):
                violations.append(Violation(
                    code="CLICK_COORD_NON_NUMERIC",
                    message="click_point coordinates must be numeric (bool not allowed).",
                    path=f"click_point[{j}]",
                    severity="error",
                    evidence=str(v),
                ))
                return self._regen(violations)
            elif isinstance(v, (int, float)):
                coords.append(float(v))
            elif self.enable_autofix and isinstance(v, str):
                stripped = v.strip().lstrip("-")
                if stripped.replace(".", "", 1).isdigit():
                    coords.append(float(v.strip()))
                    violations.append(Violation(
                        code="CLICK_COORD_COERCED",
                        message="Coerced click_point coordinate string to number.",
                        path=f"click_point[{j}]",
                        severity="warn",
                    ))
                else:
                    violations.append(Violation(
                        code="CLICK_COORD_NON_NUMERIC",
                        message="click_point coordinates must be numeric.",
                        path=f"click_point[{j}]",
                        severity="error",
                        evidence=str(v)[:100],
                    ))
                    return self._regen(violations)
            else:
                violations.append(Violation(
                    code="CLICK_COORD_NON_NUMERIC",
                    message="click_point coordinates must be numeric.",
                    path=f"click_point[{j}]",
                    severity="error",
                    evidence=str(v)[:100],
                ))
                return self._regen(violations)

        x, y = coords[0], coords[1]

        if self.schema.image_width is not None and self.schema.image_height is not None:
            w, h = self.schema.image_width, self.schema.image_height
            cx = max(0.0, min(float(w), x))
            cy = max(0.0, min(float(h), y))
            if self.enable_autofix and (cx, cy) != (x, y):
                violations.append(Violation(
                    code="CLICK_COORD_CLAMPED",
                    message=f"Clamped click_point to image bounds ({w}x{h}).",
                    path="click_point",
                    severity="warn",
                ))
            x, y = cx, cy

        bbox = self.schema.validated_bbox
        if bbox is not None and isinstance(bbox, list) and len(bbox) == 4:
            bx1, by1, bx2, by2 = bbox
            inside = (bx1 < x < bx2) and (by1 < y < by2)
            if not inside:
                if self.enable_autofix:
                    cx = (bx1 + bx2) / 2.0
                    cy = (by1 + by2) / 2.0
                    violations.append(Violation(
                        code="CLICK_OUTSIDE_BBOX_CENTERED",
                        message=(
                            f"click_point ({x:.1f}, {y:.1f}) is outside validated_bbox "
                            f"[{bx1},{by1},{bx2},{by2}]; auto-centered to ({cx:.1f},{cy:.1f})."
                        ),
                        path="click_point",
                        severity="warn",
                    ))
                    x, y = cx, cy
                else:
                    violations.append(Violation(
                        code="CLICK_OUTSIDE_BBOX",
                        message=(
                            f"click_point ({x:.1f}, {y:.1f}) must be strictly inside "
                            f"validated_bbox [{bx1},{by1},{bx2},{by2}]."
                        ),
                        path="click_point",
                        severity="error",
                    ))

        def _to_num(v: float) -> Union[int, float]:
            return int(v) if v == int(v) else v

        fixed["click_point"] = [_to_num(x), _to_num(y)]

        has_error = any(v.severity == "error" for v in violations)
        if has_error:
            return self._regen(violations)

        return GovernResult(ok=True, fixed_output=fixed, violations=violations)

    def _regen(self, violations: List[Violation]) -> GovernResult:
        lines = [
            "ClickPointGovernor rejected the output. Re-output strictly valid JSON ONLY.",
            "Constraints:",
            "- Root MUST be a JSON object with key: click_point.",
            "- click_point MUST be [x, y] with numeric values.",
            "- click_point MUST lie STRICTLY INSIDE the validated bounding box (not on boundary).",
            "- click_point MUST lie within image bounds: 0 <= x <= width, 0 <= y <= height.",
        ]
        top = violations[:8]
        if top:
            lines.append("Top violations:")
            for v in top:
                loc = f" @ {v.path}" if v.path else ""
                lines.append(f"- {v.code}{loc}: {v.message}")

        return GovernResult(
            ok=False,
            fixed_output=None,
            violations=violations,
            needs_regen=True,
            regen_instruction="\n".join(lines),
        )