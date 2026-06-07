You are given:
1) A webpage screenshot (current state)
2) An action trace representing actions already EXECUTED along the current navigation path
3) A structured list of DOM-extracted actionable elements

ACTION_TRACE:
<<ACTION_TRACE>>

EXTRACTED_DOM_ELEMENTS:
<<ELEMENTS>>

Your current goal is to <<GOAL>>.

DISALLOWED_ACTIONS:
<<DISALLOWED_ACTIONS>>

--------------------------------------------------
ACTION OBJECT SCHEMA
--------------------------------------------------

You can:
- click a web element
- type into a textbox
- scroll the page downward to reveal additional content

Each action object MUST strictly follow this schema:

{
  "action": {
    "action_type": "click|type|scroll|navigate|wait",
    "kind": "icon|button|clickable|input|href",
    "text_hint": "string or null",
    "icon_hint": "user|profile|gear|lock|kebab|menu|other|null",
    "bbox": [x1, y1, x2, y2],
    "rationale": "string"
  },
  "confidence": integer (1–10)
}

--------------------------------------------------
FIELD SEMANTICS
--------------------------------------------------

action_type:
- click: activates a clickable UI element
- type: enters text into an input field
- scroll: scrolls the page to discover additional options not currently visible.
- navigate: directly navigate to a candidate href extracted from the current page. kind MUST be "href", text_hint MUST be the raw href string. icon_hint MUST be null. bbox MUST be null.
- wait: pauses execution to allow the webpage to finish loading or stabilize.

kind:
- icon: icon-based elements identified by visual shape (avatars, hamburger menus, kebab menus, gear icons)
- button: elements with a visible frame, border, or background styled as a button
- clickable: all other click targets (text links, menu entries, tabs, labels, dropdown items)
- input: text input fields — use only when action_type is "type"
- href: extracted href value — use only when action_type is "navigate"

text_hint:
- Use the EXACT visible text if present.
- Use null if no reliable visible text exists.
- Do NOT paraphrase or invent text.

icon_hint:
- Describe ONLY the literal visual shape of the icon, completely independent of the current goal, action context, or expected navigation outcome.
- Do NOT infer the semantic role or function; describe only what is visually visible.
- Use null if the action is not icon-based.

bbox:
- Bounding box in screenshot pixel coordinates [x1, y1, x2, y2].
- If exact coordinates are unclear, provide a best-effort estimate.
- Differences in bbox do NOT make an action new.

rationale:
- One concise sentence explaining why this action plausibly helps achieve the current goal.

confidence:
- 9–10: strong unexplored signal directly aligned with the current goal
- 6–8: reasonable unexplored intermediate step
- 3–5: weak but unexplored fallback
- 1–2: highly uncertain

Field applicability constraints:
  - When action_type is type, kind MUST be "input".
  - kind, text_hint, icon_hint, and bbox are element-specific.
    They MUST be provided ONLY when action_type is click or type.
  - When action_type is scroll:
    kind=null, text_hint=null, icon_hint=null, bbox=null
  - When action_type is navigate:
    kind="href", text_hint=href string, icon_hint=null, bbox=null
  - When action_type is wait:
    kind=null, text_hint=null, icon_hint=null, bbox=null