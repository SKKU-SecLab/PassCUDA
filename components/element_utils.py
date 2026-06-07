from selenium.webdriver.common.by import By

_JS_COMMON = r"""
    // ── args ────────────────────────────────────────────────────────────────
    const VIEW      = {x1: arguments[0], y1: arguments[1], x2: arguments[2], y2: arguments[3]};
    const PAD           = arguments[4];
    const MIN_AREA      = arguments[5];
    const MIN_ELEM_AREA = arguments[6];
    const INCLUDE_NON   = arguments[7];
    const MAX_RESULTS   = arguments[8];

    const vx1 = VIEW.x1 - PAD, vy1 = VIEW.y1 - PAD,
          vx2 = VIEW.x2 + PAD, vy2 = VIEW.y2 + PAD;
    const viewBox = [vx1, vy1, vx2, vy2];

    // ── geometry ────────────────────────────────────────────────────────────
    function rectOf(el) {
        const r = el.getBoundingClientRect();
        return [r.left, r.top, r.right, r.bottom];
    }
    function area(b) {
        return Math.max(0, b[2]-b[0]) * Math.max(0, b[3]-b[1]);
    }
    function interArea(a, b) {
        const x1 = Math.max(a[0],b[0]), y1 = Math.max(a[1],b[1]);
        const x2 = Math.min(a[2],b[2]), y2 = Math.min(a[3],b[3]);
        return Math.max(0,x2-x1) * Math.max(0,y2-y1);
    }

    // ── visibility ──────────────────────────────────────────────────────────
    function isVisible(el) {
        var st = getComputedStyle(el);
        if (!st) return false;
        if (st.visibility === "hidden" || st.display === "none" ||
            parseFloat(st.opacity || "1") === 0) return false;

        var r = el.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) return false;

        
        var cur = el;
        while (cur && cur !== document.body) {
            if (cur.getAttribute("aria-hidden") === "true") return false;
            if (cur.hasAttribute("inert")) return false;
            cur = cur.parentElement;
        }

        return true;
    }

    // ── text / attrs ────────────────────────────────────────────────────────
    //function getText(el) {
    //    const tag = (el.tagName || "").toLowerCase();
    //    if (tag === "input" || tag === "textarea")
    //        return (el.getAttribute("placeholder") || el.value || "").trim();
    //    return (el.innerText || el.textContent || "").trim().slice(0, 120);
    //}
    function getText(el) {
        const tag = (el.tagName || "").toLowerCase();

        if (tag === "input" || tag === "textarea")
            return (el.getAttribute("placeholder") || el.value || "").trim();

        const labelledby = el.getAttribute && el.getAttribute("aria-labelledby");
        if (labelledby) {
            const doc = el.ownerDocument || document;
            const text = labelledby.trim().split(/\s+/)
                .map(id => {
                    const ref = doc.getElementById(id);
                    return ref ? (ref.innerText || ref.textContent || "").trim() : "";
                })
                .filter(Boolean)
                .join(" ");
            if (text) return text.slice(0, 120);
        }

        // 3) innerText
        const inner = (el.innerText || el.textContent || "").trim();
        if (inner) return inner.slice(0, 120);

        const title = el.getAttribute && el.getAttribute("title");
        return (title || "").trim().slice(0, 120);
    }
    function pickAttrs(el) {
        const keep = [
            "aria-label","aria-labelledby","aria-describedby","aria-expanded","aria-controls",
            "role","tabindex","title",
            "id","name","type","value","placeholder","autocomplete","for",
            "href","src","alt",
            "data-testid","data-test","data-qa","data-cy"
        ];
        const out = {};
        for (const k of keep) {
            const v = el.getAttribute && el.getAttribute(k);
            if (v != null && v !== "") out[k] = v;
        }
        return out;
    }

    // ── shadow traversal ────────────────────────────────────────────────────
    function buildHostSelector(host) {
        if (host.id) return `#${CSS.escape(host.id)}`;
        const dt = host.getAttribute && host.getAttribute("data-testid");
        if (dt) return `[data-testid="${CSS.escape(dt)}"]`;
        const nm = host.getAttribute && host.getAttribute("name");
        if (nm) return `${host.tagName.toLowerCase()}[name="${CSS.escape(nm)}"]`;
        const al = host.getAttribute && host.getAttribute("aria-label");
        if (al) return `${host.tagName.toLowerCase()}[aria-label="${CSS.escape(al)}"]`;
        return host.tagName.toLowerCase();
    }
    function collectAllWithShadow(root, chain, depth) {
        depth = depth || 0;
        if (depth > 5) return [];
        const out = [];
        // nodeType 11 = ShadowRoot; use ownerDocument for createTreeWalker
        const doc = (root.nodeType === 11) ? root.ownerDocument : (root.ownerDocument || root);
        const walker = doc.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
        let node = walker.nextNode(); // skip root itself
        while (node) {
            out.push({el: node, chain: chain});
            if (node.shadowRoot)
                out.push(...collectAllWithShadow(
                    node.shadowRoot,
                    chain.concat([{hostSel: buildHostSelector(node)}]),
                    depth + 1
                ));
            node = walker.nextNode();
        }
        return out;
    }

    // ── shadow-piercing elementFromPoint ────────────────────────────────────
    function deepElementFromPoint(x, y, root, depth) {
        depth = depth || 0;
        if (depth > 5) return root.elementFromPoint(x, y);
        const el = root.elementFromPoint(x, y);
        if (!el) return null;
        if (el.shadowRoot) return deepElementFromPoint(x, y, el.shadowRoot, depth + 1);
        return el;
    }

    // ── item builder ────────────────────────────────────────────────────────
    function makeItem(el, chain, ovA, kind) {
        return {
            _reason:      "overlap",
            bbox:         rectOf(el),
            overlap_area: ovA,
            tag:          (el.tagName || "").toLowerCase(),
            kind:         kind,
            role:         (el.getAttribute && el.getAttribute("role")) || "",
            text:         getText(el),
            attrs:        pickAttrs(el),
            type:         (el.getAttribute && el.getAttribute("type")) || "",
            el:           el,
            _shadow_chain: (chain && chain.length) ? chain : null,
        };
    }
"""

JS_type = _JS_COMMON + r"""
    const all  = collectAllWithShadow(document, []);
    const hits = {inputs:[], clickables:[], others:[]};

    for (const {el, chain} of all) {
        if (!el || !el.getBoundingClientRect) continue;

        const tag = (el.tagName || "").toLowerCase();
        if (tag !== "input" && tag !== "textarea" && tag !== "select") continue;
        if (!isVisible(el)) continue;

        const b    = rectOf(el);
        if (area(b) < MIN_ELEM_AREA) continue;
        const ovA  = interArea(b, viewBox);
        if (ovA < MIN_AREA) continue;

        hits.inputs.push(makeItem(el, chain, ovA, "input"));
        if (hits.inputs.length >= MAX_RESULTS) break;
    }
    return hits;
"""

JS_click_top = _JS_COMMON + r"""
    function isClickTarget(el) {
        const r  = el.getBoundingClientRect();
        const cx = r.left + r.width  / 2;
        const cy = r.top  + r.height / 2;
        if (cx < 0 || cy < 0 || cx > window.innerWidth || cy > window.innerHeight)
            return false;
        const topEl = deepElementFromPoint(cx, cy, document);
        if (!topEl) return false;
        if (el === topEl || el.contains(topEl)) return true;
        let root = topEl.getRootNode();
        while (root instanceof ShadowRoot) {
            if (root.host === el || el.contains(root.host)) return true;
            root = root.host.getRootNode();
        }
        return false;
    }

    function isLooseClickable(el) {
        const tag  = (el.tagName || "").toLowerCase();
        const role = el.getAttribute && el.getAttribute("role");

        if (tag === "button") return true;
        if (tag === "a" && el.getAttribute("href")) return true;
        if (tag === "label" && el.getAttribute("for")) return true;
        if (tag === "input") {
            const t = (el.getAttribute("type") || "").toLowerCase();
            return ["button","submit","reset","image"].includes(t);
        }
        if (role && ["button","link","menuitem","tab","option"].includes(role)) return true;

        const tabindex = el.getAttribute && el.getAttribute("tabindex");
        if (tabindex !== null && !isNaN(parseInt(tabindex)) && parseInt(tabindex) >= 0)
            return true;

        if (el.onclick || el.onmousedown || el.onmouseup) return true;

        try { if (getComputedStyle(el).cursor === "pointer") return true; } catch(e){}

        if (el.hasAttribute("data-testid") || el.hasAttribute("data-test") ||
            el.hasAttribute("data-qa")     || el.hasAttribute("data-cy")   ||
            el.hasAttribute("data-action")) return true;

        const ariaPressed  = el.getAttribute && el.getAttribute("aria-pressed");
        const ariaExpanded = el.getAttribute && el.getAttribute("aria-expanded");
        if (ariaPressed !== null || ariaExpanded !== null) return true;

        if (role && role !== "presentation" && role !== "none") return true;

        return false;
    }

    const all  = collectAllWithShadow(document, []);
    const hits = {inputs:[], clickables:[], others:[]};

    for (const {el, chain} of all) {
        if (!el || !el.getBoundingClientRect) continue;
        if (!isVisible(el))       continue;
        if (!isLooseClickable(el)) continue;
        if (!isClickTarget(el))   continue;

        const b   = rectOf(el);
        if (area(b) < MIN_ELEM_AREA) continue;
        const ovA = interArea(b, viewBox);
        if (ovA < MIN_AREA) continue;

        hits.clickables.push(makeItem(el, chain, ovA, "clickable"));
        if (hits.clickables.length >= MAX_RESULTS) break;
    }
    return hits;
"""

JS_click = _JS_COMMON + r"""
    function isLooseClickable(el) {
        const tag  = (el.tagName || "").toLowerCase();
        const role = el.getAttribute && el.getAttribute("role");

        if (tag === "button") return true;
        if (tag === "a" && el.getAttribute("href")) return true;
        if (tag === "label" && el.getAttribute("for")) return true;
        if (tag === "input") {
            const t = (el.getAttribute("type") || "").toLowerCase();
            return ["button","submit","reset","image"].includes(t);
        }
        if (role && ["button","link","menuitem","tab","option"].includes(role)) return true;

        const tabindex = el.getAttribute && el.getAttribute("tabindex");
        if (tabindex !== null && !isNaN(parseInt(tabindex)) && parseInt(tabindex) >= 0)
            return true;

        if (el.onclick || el.onmousedown || el.onmouseup) return true;

        try { if (getComputedStyle(el).cursor === "pointer") return true; } catch(e){}

        if (el.hasAttribute("data-testid") || el.hasAttribute("data-test") ||
            el.hasAttribute("data-qa")     || el.hasAttribute("data-cy")   ||
            el.hasAttribute("data-action")) return true;

        const ariaPressed  = el.getAttribute && el.getAttribute("aria-pressed");
        const ariaExpanded = el.getAttribute && el.getAttribute("aria-expanded");
        if (ariaPressed !== null || ariaExpanded !== null) return true;

        if (role && role !== "presentation" && role !== "none") return true;

        return false;
    }

    const all  = collectAllWithShadow(document, []);
    const hits = {inputs:[], clickables:[], others:[]};

    for (const {el, chain} of all) {
        if (!el || !el.getBoundingClientRect) continue;
        if (!isVisible(el))        continue;
        if (!isLooseClickable(el)) continue;

        const b   = rectOf(el);
        if (area(b) < MIN_ELEM_AREA) continue;
        const ovA = interArea(b, viewBox);
        if (ovA < MIN_AREA) continue;

        hits.clickables.push(makeItem(el, chain, ovA, "clickable"));
        if (hits.clickables.length >= MAX_RESULTS) break;
    }
    return hits;
"""

def ret_quadrant_bbox(driver, bbox, *, use_bbox_center=True, padding=100):
    x1, y1, x2, y2 = map(float, bbox)

    driver.switch_to.default_content()

    vw = driver.execute_script("return window.innerWidth;")
    vh = driver.execute_script("return window.innerHeight;")

    win_w = vw / 2.0
    win_h = vh / 2.0

    bbox_w = x2 - x1
    bbox_h = y2 - y1

    cx = (x1 + x2) / 2.0 if use_bbox_center else x1
    cy = (y1 + y2) / 2.0 if use_bbox_center else y1

    if bbox_w > win_w:
        win_w = bbox_w + padding
    if bbox_h > win_h:
        win_h = bbox_h + padding

    left = cx - win_w / 2.0
    top  = cy - win_h / 2.0

    left = max(0.0, min(left, vw - win_w))
    top  = max(0.0, min(top,  vh - win_h))

    right  = left + win_w
    bottom = top  + win_h

    return [left, top, right, bottom]

def ret_padded_bbox(driver, bbox, pixel=50, *, keep_center=True):

    x1, y1, x2, y2 = map(float, bbox)

    vw = driver.execute_script("return window.innerWidth;")
    vh = driver.execute_script("return window.innerHeight;")

    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0

    width  = x2 - x1
    height = y2 - y1

    new_w = width  + 2 * pixel
    new_h = height + 2 * pixel

    if keep_center:
        left = cx - new_w / 2.0
        top  = cy - new_h / 2.0
    else:
        left = x1 - pixel
        top  = y1 - pixel

    right  = left + new_w
    bottom = top  + new_h

    left   = max(0.0, left)
    top    = max(0.0, top)
    right  = min(vw, right)
    bottom = min(vh, bottom)

    return [left, top, right, bottom]

def convert_bbox_to_crop_space(bbox, crop_bbox):
    x1, y1, x2, y2 = bbox
    left, top, _, _ = crop_bbox

    return [
        x1 - left,
        y1 - top,
        x2 - left,
        y2 - top,
    ]

def convert_crop_bbox_to_viewport(relative_bbox, crop_bbox):
    """
    relative_bbox: [x1, y1, x2, y2]
    crop_bbox: [left, top, right, bottom]

    return: [x1, y1, x2, y2]
    """
    x1, y1, x2, y2 = relative_bbox
    left, top, _, _ = crop_bbox

    return [
        x1 + left,
        y1 + top,
        x2 + left,
        y2 + top,
    ]

def convert_crop_point_to_viewport(click_point, crop_bbox):
    """
    click_point: [x, y]
    crop_bbox: [left, top, right, bottom]

    return: [x, y]
    """
    x, y = click_point
    left, top, _, _ = crop_bbox

    return [
        x + left,
        y + top,
    ]

def clamp_bbox_to_image(bbox, image_size):
    """
    bbox: [x1, y1, x2, y2]
    image_size: {'width': W, 'height': H}

    return: bbox clamped to image bounds
    """
    x1, y1, x2, y2 = bbox
    W = image_size['width']
    H = image_size['height']

    x1 = max(0, min(x1, W))
    y1 = max(0, min(y1, H))
    x2 = max(0, min(x2, W))
    y2 = max(0, min(y2, H))

    if x1 > x2:
        x1, x2 = x2, x1
    if y1 > y2:
        y1, y2 = y2, y1

    return [x1, y1, x2, y2]

def collect_overllaping_elements(
    driver,
    top_bbox,  # TOP viewport coords [x1,y1,x2,y2]
    action_type,
    *,
    pad=0,
    min_area=0,
    min_elem_area=0,
    max_results=300,
    include_noninteractive=True,
    max_depth=2,
):
    """
    Returns hits dict:
      {inputs: [...], clickables: [...], others: [...]}

    Each item contains:
      - bbox          : [x1,y1,x2,y2] in TOP viewport coords
      - tag / kind / text / attrs / type / role
      - el            : WebElement (None for cross-origin iframe fallbacks)
      - _frame_path   : ["top", i, ...]   — iframe index chain from TOP
      - _shadow_chain : [{hostSel},...] | None
      - _cross_origin : True if collected via driver fallback (not JS)
    """

    def intersect(a, b):
        x1 = max(a[0], b[0]); y1 = max(a[1], b[1])
        x2 = min(a[2], b[2]); y2 = min(a[3], b[3])
        return [x1, y1, x2, y2] if x2 > x1 and y2 > y1 else None

    def offset_bbox(b, dx, dy):
        return [b[0]+dx, b[1]+dy, b[2]+dx, b[3]+dy]

    def merge_hits(dst, src):
        for k in ("inputs", "clickables", "others"):
            dst[k].extend(src.get(k, []))

    def collect_via_js(view_bbox, action_type):
        x1, y1, x2, y2 = map(float, view_bbox)
        js = JS_type if action_type == "type" else JS_click_top
        return driver.execute_script(
            js,
            x1, y1, x2, y2,
            float(pad), float(min_area), float(min_elem_area),
            bool(include_noninteractive), int(max_results),
        )

    _CLICK_TAGS  = {"button", "a", "input", "label", "select"}
    _INPUT_TAGS  = {"input", "textarea", "select"}
    _CLICK_TYPES = {"button", "submit", "reset", "image"}

    def _item_from_element(el, frame_path, fr_offset, action_type):
        """Convert a WebElement to a hits-item dict. Returns None if unusable."""
        try:
            rect = driver.execute_script(
                "const r=arguments[0].getBoundingClientRect();"
                "return [r.left,r.top,r.right,r.bottom,r.width,r.height];", el
            )
        except Exception:
            return None
        if not rect or rect[4] <= 0 or rect[5] <= 0:
            return None

        bbox = offset_bbox(rect[:4], fr_offset[0], fr_offset[1])

        ov = intersect(bbox, list(top_bbox))
        ov_area = 0.0
        if ov:
            ov_area = max(0, ov[2]-ov[0]) * max(0, ov[3]-ov[1])
        if ov_area < min_area:
            return None

        try:
            tag  = (el.tag_name or "").lower()
            role = (el.get_attribute("role") or "").lower()
            text = (el.text or "").strip()[:120]
            attrs = {
                k: el.get_attribute(k)
                for k in ("id","name","type","placeholder","aria-label",
                          "href","data-testid","tabindex","role","title","value")
                if el.get_attribute(k)
            }
            el_type = (el.get_attribute("type") or "").lower()
        except Exception:
            return None

        if action_type == "type":
            if tag not in _INPUT_TAGS:
                return None
            kind = "input"
        else:
            is_click = (
                tag in _CLICK_TAGS or
                role in ("button", "link", "menuitem", "tab", "option") or
                (tag == "input" and el_type in _CLICK_TYPES)
            )
            if not is_click:
                return None
            kind = "input" if tag in _INPUT_TAGS else "clickable"

        return {
            "_reason":       "overlap",
            "bbox":          bbox,
            "overlap_area":  ov_area,
            "tag":           tag,
            "kind":          kind,
            "role":          role,
            "text":          text,
            "attrs":         attrs,
            "type":          el_type,
            "el":            el,
            "_frame_path":   frame_path[:],
            "_shadow_chain": None,
            "_cross_origin": True,
        }

    def collect_cross_origin_iframe(fr, frame_path, fr_rect, action_type):
        """
        Switch into a cross-origin iframe and collect elements via find_elements.
        Returns a hits dict.
        """
        hits = {"inputs": [], "clickables": [], "others": []}
        fr_offset = (fr_rect["left"], fr_rect["top"])
        try:
            driver.switch_to.frame(fr)
            if action_type == "type":
                css = "input, textarea, select"
            else:
                css = ("button, a[href], input[type=button], input[type=submit],"
                       " input[type=reset], [role=button], [role=link],"
                       " [role=menuitem], [role=tab], [tabindex]")
            elements = driver.find_elements(By.CSS_SELECTOR, css)
            for el in elements:
                item = _item_from_element(el, frame_path, fr_offset, action_type)
                if item is None:
                    continue
                bucket = "inputs" if item["kind"] == "input" else "clickables"
                hits[bucket].append(item)
                if sum(len(v) for v in hits.values()) >= max_results:
                    break
        except Exception:
            pass
        finally:
            try:
                driver.switch_to.default_content()
            except Exception:
                pass
        return hits

    def walk_frames(frame_path, depth):
        if depth >= max_depth:
            return

        frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
        for i, fr in enumerate(frames):
            child_path = frame_path + [i]
            try:
                fr_rect = driver.execute_script(
                    "const r=arguments[0].getBoundingClientRect();"
                    "return {left:r.left,top:r.top,right:r.right,bottom:r.bottom,"
                    "        width:r.width,height:r.height};", fr
                )
                if not fr_rect or fr_rect["width"] <= 0 or fr_rect["height"] <= 0:
                    continue

                iframe_bbox = [fr_rect["left"], fr_rect["top"],
                               fr_rect["right"], fr_rect["bottom"]]

                if depth == 0:
                    ov = intersect(top_bbox, iframe_bbox)
                    if ov is None:
                        continue
                    inner_bbox = [ov[0]-fr_rect["left"], ov[1]-fr_rect["top"],
                                  ov[2]-fr_rect["left"], ov[3]-fr_rect["top"]]
                    fr_offset  = (fr_rect["left"], fr_rect["top"])
                else:
                    inner_bbox = None
                    fr_offset  = None

                entered_frame = False
                try:
                    driver.switch_to.frame(fr)
                    entered_frame = True

                    if inner_bbox is None:
                        fw = driver.execute_script("return window.innerWidth;")
                        fh = driver.execute_script("return window.innerHeight;")
                        inner_bbox = [0, 0, fw, fh]

                    inner_hits = collect_via_js(inner_bbox, action_type)

                    for k in ("inputs", "clickables", "others"):
                        for it in inner_hits.get(k, []):
                            it["_frame_path"]    = child_path[:]
                            it["_cross_origin"]  = False
                            if depth == 0 and fr_offset:
                                it["_frame_bbox_top"] = iframe_bbox
                                it["bbox"] = offset_bbox(it["bbox"], fr_offset[0], fr_offset[1])
                            else:
                                it["_frame_bbox_top"] = None

                    merge_hits(out, inner_hits)
                    walk_frames(child_path, depth + 1)
                    driver.switch_to.parent_frame()
                    entered_frame = False

                except Exception:
                    if entered_frame:
                        try:
                            driver.switch_to.parent_frame()
                        except Exception:
                            pass
                        entered_frame = False
                    co_hits = collect_cross_origin_iframe(
                        fr, child_path, fr_rect, action_type
                    )
                    merge_hits(out, co_hits)

            except Exception:
                pass  # fr_rect fetch failed — no frame was entered, nothing to restore

    driver.switch_to.default_content()
    out = {"inputs": [], "clickables": [], "others": []}

    top_hits = collect_via_js(top_bbox, action_type)
    for k in ("inputs", "clickables", "others"):
        for it in top_hits.get(k, []):
            it["_frame_path"]   = ["top"]
            it["_cross_origin"] = False
    merge_hits(out, top_hits)

    walk_frames(["top"], 0)

    driver.switch_to.default_content()
    return out

def flatten_elements(hits, *, include_others=False):
  all_items = []

  all_items.extend(hits.get("inputs", []))
  all_items.extend(hits.get("clickables", []))

  if include_others:
      all_items.extend(hits.get("others", []))

  all_items.sort(key=lambda x: x.get("overlap_area", 0), reverse=True)

  return all_items

def get_viewport_wh(driver):
    return driver.execute_script("""
        return [window.innerWidth, window.innerHeight];
    """)

def drop_giant_containers(items, *, viewport_wh=None, max_ratio=0.6):
    out = []
    for it in items:
        x1,y1,x2,y2 = it["bbox"]
        area = max(0, x2-x1) * max(0, y2-y1)
        if it.get("tag") in ("html","body"):
            continue
        if viewport_wh:
            W,H = viewport_wh
            if area >= (W*H*max_ratio):
                continue
        out.append(it)
    return out

def _switch_to_frame_path(driver, frame_path):
    """
    frame_path: ["top", i, j, ...]
    """
    driver.switch_to.default_content()
    if not frame_path or frame_path == ["top"]:
        return
    for idx in frame_path[1:]:
        frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
        if idx < 0 or idx >= len(frames):
            raise RuntimeError(f"frame index out of range: {idx} / {len(frames)}")
        driver.switch_to.frame(frames[idx])

def _get_target_selector(attrs: dict, fallback_tag=None) -> str:
    if attrs.get("id"):
        return f"#{attrs['id']}"
    if attrs.get("data-testid"):
        return f"[data-testid='{attrs['data-testid']}']"
    if attrs.get("name") and attrs.get("type") and (fallback_tag or "").lower() == "input":
        return f"input[name='{attrs['name']}'][type='{attrs['type']}']"
    if attrs.get("name"):
        return f"[name='{attrs['name']}']"
    if attrs.get("aria-label"):
        return f"[aria-label='{attrs['aria-label']}']"
    if attrs.get("href"):
        return f"[href='{attrs['href']}']"
    if attrs.get("title"):
        return f"[title='{attrs['title']}']"
    if attrs.get("type"):
        return f"[type='{attrs['type']}']"

    if fallback_tag:
        return fallback_tag
    return "input,textarea,select,button,a"

def _find_in_shadow_chain(driver, shadow_chain, target_selector):
    """
    shadow_chain: [{"hostSel": "..."} , ...]
    """
    js = r"""
    const chain = arguments[0];
    const targetSel = arguments[1];

    let root = document;
    for (const step of chain){
      const host = root.querySelector(step.hostSel);
      if (!host) return null;
      root = host.shadowRoot;
    }
    return root.querySelector(targetSel);
    """
    return driver.execute_script(js, shadow_chain, target_selector)

def _get_shadow_host(driver, shadow_chain):
    js = r"""
    const chain = arguments[0];
    let root = document;
    let host = null;
    for (const step of chain){
      host = root.querySelector(step.hostSel);
      if (!host) return null;
      
      root = host.shadowRoot;
    }
    return host;
    """
    return driver.execute_script(js, shadow_chain)

KEEP_ATTR_EXACT = {
    "aria-label", "aria-labelledby", "aria-describedby", "aria-expanded", "aria-controls",
    "role", "tabindex", "title",

    "id", "name", "type", "value", "placeholder", "autocomplete", "for",

    "href", "src", "alt",

    "data-testid", "data-test", "data-qa", "data-cy",
}
from bs4 import BeautifulSoup

def strip_html_attributes(html: str, keep_attrs=KEEP_ATTR_EXACT) -> str:
    """
    html: outerHTML string
    """
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all(True):
        if not tag.attrs:
            continue

        new_attrs = {}
        for k, v in tag.attrs.items():
            if k in keep_attrs:
                new_attrs[k] = v

        tag.attrs = new_attrs
    return str(soup)

def sanitize_elements(elements, driver=None, *, key="el", max_html=300):
    def iter_items(obj):
        if isinstance(obj, dict):
            for g in ("inputs", "clickables", "others"):
                for it in obj.get(g, []):
                    if isinstance(it, dict):
                        yield it
        elif isinstance(obj, list):
            for it in obj:
                if isinstance(it, dict):
                    yield it

    lines = []
    raw_htmls = []
    idx = 0

    for item in iter_items(elements):
        idx += 1

        if driver is not None:
            frame_path = item.get("_frame_path", ["top"])
            try:
                _switch_to_frame_path(driver, frame_path)
            except Exception:
                driver.switch_to.default_content()

        shadow_chain = item.get("_shadow_chain")
        el = item.get(key) or item.get("el")

        if el is None and shadow_chain and driver is not None:
            attrs = item.get("attrs", {}) or {}
            fallback_tag = item.get("tag")
            target_sel = _get_target_selector(attrs, fallback_tag=fallback_tag)
            try:
                el = _find_in_shadow_chain(driver, shadow_chain, target_sel)

                if el is None:
                    host = _get_shadow_host(driver, shadow_chain)
                    if host is not None:
                        el = host
            except Exception:
                el = None

        if el is None:
            if driver is not None:
                driver.switch_to.default_content()
            lines.append(f"{idx}. None")
            continue

        html = None
        if driver is not None:
            try:
                html = driver.execute_script("return arguments[0].outerHTML;", el)
            except Exception:
                try:
                    html = el.get_attribute("outerHTML")
                except Exception:
                    html = None
        else:
            try:
                html = el.get_attribute("outerHTML")
            except Exception:
                html = None

        if driver is not None:
            driver.switch_to.default_content()

        if not html:
            continue
        raw_htmls.append(html)

        html = strip_html_attributes(html)
        if len(html) > max_html:
            html = html[:max_html] + "...(truncated)"

        fp = item.get("_frame_path", ["top"])
        sc = "Y" if shadow_chain else "N"

        context = None
        if driver is not None:
            try:
                context = driver.execute_script(r"""
                    const el = arguments[0];
                    let cur = el.parentElement;
                    for (let i = 0; i < 6; i++) {
                        if (!cur) break;
                        const dg  = cur.getAttribute("data-group");
                        const id  = cur.getAttribute("id");
                        if (dg) return dg;
                        if (id) return id;
                        cur = cur.parentElement;
                    }
                    return null;
                """, el)
            except Exception:
                context = None

        bbox = item.get("bbox")
        bbox_str = f" | bbox=[{int(bbox[0])},{int(bbox[1])},{int(bbox[2])},{int(bbox[3])}]" if bbox else ""
        ctx_str  = f" | context={context}" if context else ""
        lines.append(f"[{idx}]. {html}{ctx_str}{bbox_str}")

    return lines, raw_htmls

def switch_to_frame_path(driver, frame_path):
    driver.switch_to.default_content()
    if not frame_path or frame_path == ["top"]:
        return
    for idx in frame_path[1:]:
        frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
        if idx < 0 or idx >= len(frames):
            raise RuntimeError(f"Frame index out of range: {idx} / {len(frames)}")
        driver.switch_to.frame(frames[idx])

def build_target_selector_from_item(item) -> str:
    attrs = item.get("attrs", {}) or {}
    tag = (item.get("tag") or "").lower()

    if attrs.get("id"):
        return f"#{attrs['id']}"
    if attrs.get("data-testid"):
        return f"[data-testid='{attrs['data-testid']}']"
    if tag == "input" and attrs.get("name") and attrs.get("type"):
        return f"input[name='{attrs['name']}'][type='{attrs['type']}']"
    if attrs.get("name"):
        return f"[name='{attrs['name']}']"
    if attrs.get("aria-label"):
        return "[aria-label]"
    if attrs.get("href"):
        return f"[href='{attrs['href']}']"
    if attrs.get("title"):
        return f"[title='{attrs['title']}']"
    if attrs.get("type"):
        return f"[type='{attrs['type']}']"
    return f"{tag}" if tag else "input,textarea,select,button,a,[role='button'],[role='link'],[tabindex]"

from selenium.common.exceptions import WebDriverException

def resolve_action_element(driver, item):
    """
    item: filtered_elements[...] dict
    return: (webelement, is_shadow)
    """
    frame_path = item.get("_frame_path", ["top"])
    shadow_chain = item.get("_shadow_chain")

    switch_to_frame_path(driver, frame_path)

    el = item.get("el")
    if shadow_chain is None:
        if el is None:
            sel = build_target_selector_from_item(item)
            el = driver.find_element(By.CSS_SELECTOR, sel)

        driver.switch_to.default_content()

        return el, False

    driver.switch_to.default_content()

    return el, True

def extract_element_features(el):
    def safe_attr(name):
        try:
            return el.get_attribute(name) or ""
        except Exception:
            return ""

    try:
        text = (el.text or "").strip()
    except Exception:
        text = ""

    return {
        "text": text,
        "aria-label": safe_attr("aria-label"),
        "id": safe_attr("id"),
        "name": safe_attr("name"),
        "role": safe_attr("role"),
        "title": safe_attr("title"),
        "placeholder": safe_attr("placeholder"),
        "href": safe_attr("href"),
        "tag": el.tag_name.lower() if el.tag_name else "",
    }

def extract_item_features(item):
    attrs = item.get("attrs", {}) or {}

    return {
        "text": (item.get("text") or "").strip(),
        "aria-label": attrs.get("aria-label", ""),
        "id": attrs.get("id", ""),
        "name": attrs.get("name", ""),
        "role": attrs.get("role", "") or item.get("role", ""),
        "title": attrs.get("title", ""),
        "placeholder": attrs.get("placeholder", ""),
        "href": attrs.get("href", ""),
        "tag": (item.get("tag") or "").lower(),
    }

def norm(s: str) -> str:
    return " ".join(s.lower().split())

def partial_match(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    a, b = norm(a), norm(b)
    if a == b:
        return 1.0
    if a in b or b in a:
        return 1.0
    at = set(a.split())
    bt = set(b.split())
    if at & bt:
        return 0.5
    return 0.0

def partial_match_strict(a: str, b: str) -> float:
    if not a or not b:
        return 0.0

    a = norm(a)
    b = norm(b)

    at = a.split()
    bt = b.split()

    if all(tok in bt for tok in at):
        return 1.0

    return 0.0

FEATURE_WEIGHTS = {
    "text": 5.0,
    "aria-label": 4.0,
    "id": 4.0,
    "name": 3.0,
    "role": 2.0,
    "title": 2.0,
    "placeholder": 2.0,
    "href": 1.5,
    "tag": 1.0,
}

def score_candidate(item_feat, el_feat):
    score = 0.0
    for k, w in FEATURE_WEIGHTS.items():
        s = partial_match(item_feat.get(k, ""), el_feat.get(k, ""))
        score += w * s
    return score

def score_candidate_href(item_feat, el_feat):
    score = 0.0
    for k, w in FEATURE_WEIGHTS.items():
        s = partial_match_strict(item_feat.get(k, ""), el_feat.get(k, ""))
        score += w * s
    return score

def pick_best_element_by_similarity(item, candidates):
    item_feat = extract_item_features(item)

    best_el = None
    best_score = -1.0

    for el in candidates:
        try:
            el_feat = extract_element_features(el)
            sc = score_candidate(item_feat, el_feat)
            if sc > best_score:
                best_score = sc
                best_el = el
        except Exception:
            continue

    return best_el

def resolve_action_element_from_item(driver, item):
    """
    item: dict (el may be raw HTML string)
    return: WebElement
    """
    frame_path = item.get("_frame_path", ["top"])

    driver.switch_to.default_content()
    for idx in frame_path[1:]:
        frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
        if idx < 0 or idx >= len(frames):
            raise RuntimeError(f"Frame index out of range: {idx}")
        driver.switch_to.frame(frames[idx])

    selector = build_target_selector_from_item(item)
    candidates = driver.find_elements(By.CSS_SELECTOR, selector)

    if not candidates:
        raise RuntimeError(f"No element found for selector: {selector}")

    driver.switch_to.default_content()

    return pick_best_element_by_similarity(item, candidates)

def get_element_bbox(driver, el_or_item):
    """
    Return bbox in TOP viewport coordinates: [x1, y1, x2, y2].

    Accepts either:
      - a raw WebElement  (legacy — assumes caller has set correct frame context)
      - a filtered_elements item dict  (handles iframe switching automatically)

    When an item dict is passed the function:
      1. Switches to the correct iframe via _frame_path.
      2. Calls getBoundingClientRect() inside that frame.
      3. Adds the frame TOP-viewport offset so bbox is always in TOP coords.
      4. Restores driver context to default_content.
    """
    if not isinstance(el_or_item, dict):
        return driver.execute_script(
            "const r=arguments[0].getBoundingClientRect();"
            "return [r.left,r.top,r.right,r.bottom];",
            el_or_item,
        )

    item       = el_or_item
    el         = item.get("el")
    frame_path = item.get("_frame_path", ["top"])

    frame_bbox_top = item.get("_frame_bbox_top")
    if frame_bbox_top:
        dx, dy = frame_bbox_top[0], frame_bbox_top[1]
    else:
        dx, dy = 0.0, 0.0

    try:
        _switch_to_frame_path(driver, frame_path)  # noop if ["top"]
        rect = driver.execute_script(
            "const r=arguments[0].getBoundingClientRect();"
            "return [r.left,r.top,r.right,r.bottom];",
            el,
        )
    finally:
        driver.switch_to.default_content()

    return [rect[0] + dx, rect[1] + dy, rect[2] + dx, rect[3] + dy]

from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys

def click_by_point_bu(driver, click_point):
    x, y = map(float, click_point)

    actions = ActionChains(driver)
    pa = actions.w3c_actions.pointer_action

    pa.move_to_location(x, y)
    pa.pointer_down()
    pa.pointer_up()

    actions.perform()

def click_by_point(driver, click_point):
    import pyautogui
    import time

    x, y = map(float, click_point)

    win = driver.get_window_rect()
    toolbar_height = driver.execute_script(
        "return window.outerHeight - window.innerHeight"
    )
    abs_x = win['x'] + x
    abs_y = win['y'] + toolbar_height + y

    driver.disconnect()
    time.sleep(0.5)
    pyautogui.click(x=abs_x, y=abs_y)
    driver.reconnect(3)

def safe_click_by_point(driver, click_point):
    import time
    import random
    from selenium.webdriver.common.action_chains import ActionChains

    x, y = map(float, click_point)
    vw = driver.execute_script("return window.innerWidth;")
    vh = driver.execute_script("return window.innerHeight;")

    in_viewport = 0 <= x <= vw and 0 <= y <= vh
    scroll_before = None

    if not in_viewport:
        try:
            scroll_before = driver.execute_script(
                "return {x: window.scrollX, y: window.scrollY};"
            )
            target_scroll_y = y - vh / 2
            driver.execute_script(
                "window.scrollTo({top: arguments[0], left: 0, behavior: 'instant'});",
                max(0, target_scroll_y)
            )
            time.sleep(0.2)
            scroll_y = driver.execute_script("return window.scrollY;")
            y = y - scroll_y
        except Exception:
            scroll_before = None

    actions = ActionChains(driver, duration=random.randint(80, 150))
    actions.w3c_actions.pointer_action.move_to_location(x, y)
    actions.w3c_actions.pointer_action.pointer_down()
    time.sleep(random.uniform(0.05, 0.15))
    actions.w3c_actions.pointer_action.pointer_up()
    actions.perform()
    time.sleep(random.uniform(0.3, 0.7))

    if scroll_before is not None:
        try:
            driver.execute_script(
                "window.scrollTo({top: arguments[0], left: arguments[1], behavior: 'instant'});",
                scroll_before["y"], scroll_before["x"]
            )
        except Exception:
            pass

def type_by_point(driver, click_point, value, clear_first=False, press_enter=True):
    import time
    import random
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.webdriver.common.keys import Keys

    x, y = map(float, click_point)

    actions = ActionChains(driver, duration=random.randint(80, 150))
    actions.w3c_actions.pointer_action.move_to_location(x, y)
    actions.w3c_actions.pointer_action.pointer_down()
    actions.w3c_actions.pointer_action.pointer_up()
    actions.perform()

    time.sleep(random.uniform(0.3, 0.8))

    actions = ActionChains(driver)
    if clear_first:
        actions.key_down(Keys.CONTROL).send_keys("a").key_up(Keys.CONTROL)
        actions.send_keys(Keys.BACKSPACE)
        actions.perform()
        time.sleep(random.uniform(0.1, 0.2))
        actions = ActionChains(driver)

    for char in value:
        actions.send_keys(char)
        actions.perform()
        time.sleep(random.uniform(0.05, 0.15))
        actions = ActionChains(driver)

    if press_enter:
        time.sleep(random.uniform(0.3, 0.7))
        actions = ActionChains(driver)
        actions.send_keys(Keys.RETURN)
        actions.perform()

def safe_click_by_point_gui(driver, click_point):
    import pyautogui
    import time

    x, y = map(float, click_point)
    vw = driver.execute_script("return window.innerWidth;")
    vh = driver.execute_script("return window.innerHeight;")

    if not (0 <= x <= vw and 0 <= y <= vh):
        target_scroll_y = y - vh / 2
        driver.execute_script(
            "window.scrollTo({top: arguments[0], left: 0, behavior: 'instant'});",
            max(0, target_scroll_y)
        )
        time.sleep(0.2)
        scroll_y = driver.execute_script("return window.scrollY;")
        y = y - scroll_y

    win = driver.get_window_rect()
    toolbar_height = driver.execute_script("return window.outerHeight - window.innerHeight")
    abs_x = win['x'] + x
    abs_y = win['y'] + toolbar_height + y

    pyautogui.click(x=abs_x, y=abs_y)
    time.sleep(6)

def type_by_point_gui(driver, click_point, value, clear_first=False, press_enter=True):
    import pyautogui
    import time
    import random

    x, y = map(float, click_point)

    win = driver.get_window_rect()
    toolbar_height = driver.execute_script("return window.outerHeight - window.innerHeight")
    abs_x = win['x'] + x
    abs_y = win['y'] + toolbar_height + y

    pyautogui.click(x=abs_x, y=abs_y)
    time.sleep(random.uniform(0.1, 0.3))

    if clear_first:
        pyautogui.hotkey('ctrl', 'a')
        pyautogui.press('backspace')

    pyautogui.typewrite(value, interval=0.05)

    if press_enter:
        pyautogui.press('enter')

    time.sleep(6)

def type_by_point_bu(driver, click_point, value, clear_first=False, press_enter=True):

    x, y = map(float, click_point)

    actions = ActionChains(driver)
    pa = actions.w3c_actions.pointer_action

    pa.move_to_location(x, y)
    pa.pointer_down()
    pa.pointer_up()

    actions.perform()

    actions = ActionChains(driver)
    pa = actions.w3c_actions.pointer_action

    pa.release()
    pa.move_to_location(x, y)
    pa.pointer_down()
    pa.pointer_up()
    actions.perform()

    actions = ActionChains(driver)

    if clear_first:
        actions.key_down(Keys.CONTROL).send_keys("a").key_up(Keys.CONTROL)
        actions.send_keys(Keys.BACKSPACE)

    actions.send_keys(value)

    if press_enter:
        actions.send_keys(Keys.ENTER)
    actions.perform()

def type_by_bbox(driver, bbox, value, *,
                 clear_first=True,
                 press_enter=False):

    x1, y1, x2, y2 = map(float, bbox)
    cx = int((x1 + x2) / 2)
    cy = int((y1 + y2) / 2)

    actions = ActionChains(driver)
    actions.w3c_actions.pointer_action.move_to_location(cx, cy)
    actions.w3c_actions.pointer_action.click()
    actions.perform()

    actions = ActionChains(driver)

    if clear_first:
        actions.key_down(Keys.CONTROL).send_keys("a").key_up(Keys.CONTROL)
        actions.send_keys(Keys.BACKSPACE)

    actions.send_keys(str(value))

    if press_enter:
        actions.send_keys(Keys.ENTER)

    actions.perform()

    return True

from PIL import Image
import io
def save_window_bbox_png(driver, window_bbox, out_path):
    x1, y1, x2, y2 = map(int, window_bbox)

    dpr = driver.execute_script("return window.devicePixelRatio || 1;")

    png = driver.get_screenshot_as_png()
    img = Image.open(io.BytesIO(png))

    crop_box = (
        int(x1 * dpr),
        int(y1 * dpr),
        int(x2 * dpr),
        int(y2 * dpr),
    )

    cropped = img.crop(crop_box)
    cropped.save(out_path)


def highlight_element_label(driver, element, *,
                      color="#ff0066",
                      label="SELECTED ELEMENT",
                      scroll_into_view=True):

    driver.execute_script("""
        const el = arguments[0];
        const color = arguments[1];
        const labelText = arguments[2];
        const scroll = arguments[3];

        if (!el) return;

        if (scroll) {
            el.scrollIntoView({block: "center", inline: "center"});
        }

        const rect = el.getBoundingClientRect();

        
        if (!el.hasAttribute("data-_orig_style")) {
            el.setAttribute("data-_orig_style", el.getAttribute("style") || "");
        }

        
        el.style.outline = `4px solid ${color}`;
        el.style.boxShadow = `0 0 0 4px ${color}55, 0 0 15px ${color}`;
        el.style.transition = "all 0.15s ease-in-out";

        const badge = document.createElement("div");
        badge.innerText = labelText;

        badge.style.position = "fixed";
        badge.style.left = (rect.left) + "px";
        badge.style.top = (rect.top - 28) + "px";
        badge.style.background = color;
        badge.style.color = "white";
        badge.style.padding = "4px 8px";
        badge.style.fontSize = "12px";
        badge.style.fontWeight = "bold";
        badge.style.borderRadius = "6px";
        badge.style.zIndex = "2147483647";
        badge.style.pointerEvents = "none";
        badge.style.boxShadow = "0 2px 6px rgba(0,0,0,0.3)";

        badge.className = "__vlm_highlight_badge__";
        document.body.appendChild(badge);

        
        el.animate([
            { transform: "scale(1)" },
            { transform: "scale(1.02)" },
            { transform: "scale(1)" }
        ], {
            duration: 600,
            iterations: Infinity
        });

    """, element, color, label, scroll_into_view)
def highlight_element_ani(driver, element, *,
                      color="#ff0066",
                      scroll_into_view=True):

    driver.execute_script("""
        const el = arguments[0];
        const color = arguments[1];
        const scroll = arguments[2];

        if (!el) return;

        if (scroll) {
            el.scrollIntoView({block: "center", inline: "center"});
        }

        
        if (!el.hasAttribute("data-_orig_style")) {
            el.setAttribute("data-_orig_style", el.getAttribute("style") || "");
        }

        
        el.style.outline = `4px solid ${color}`;
        el.style.boxShadow = `0 0 0 4px ${color}55, 0 0 15px ${color}`;
        el.style.transition = "all 0.15s ease-in-out";

    """, element, color, scroll_into_view)
def highlight_element(driver, element,
                      color="255, 0, 102",
                      alpha=0.25,
                      border_px=1):
    driver.execute_script("""
        const el = arguments[0];
        const rgb = arguments[1];
        const alpha = arguments[2];
        const borderPx = arguments[3];

        const rect = el.getBoundingClientRect();

        
        document.querySelectorAll(".__vlm_soft_overlay__")
            .forEach(e => e.remove());

        const overlay = document.createElement("div");
        overlay.className = "__vlm_soft_overlay__";

        overlay.style.position = "fixed";
        overlay.style.left = rect.left + "px";
        overlay.style.top = rect.top + "px";
        overlay.style.width = rect.width + "px";
        overlay.style.height = rect.height + "px";

        overlay.style.background = `rgba(${rgb}, ${alpha})`;

        overlay.style.border = borderPx + "px solid rgb(" + rgb + ")";

        overlay.style.zIndex = 2147483647;
        overlay.style.pointerEvents = "none";
        overlay.style.borderRadius = "6px";

        document.documentElement.appendChild(overlay);
    """, element, color, alpha, border_px)

def unhighlight_element(driver, element):
    driver.execute_script("""
        const el = arguments[0];
        if (!el) return;

        const orig = el.getAttribute("data-_orig_style");
        if (orig !== null) {
            el.setAttribute("style", orig);
            el.removeAttribute("data-_orig_style");
        }

        document.querySelectorAll(".__vlm_highlight_badge__")
            .forEach(e => e.remove());
    """, element)

def highlight_bbox_label(driver, bbox, *,
                   color="#ff0066",
                   label="SELECTED ELEMENT"):

    x1, y1, x2, y2 = bbox

    driver.execute_script("""
        const x1 = arguments[0];
        const y1 = arguments[1];
        const x2 = arguments[2];
        const y2 = arguments[3];
        const color = arguments[4];
        const labelText = arguments[5];

        const width = x2 - x1;
        const height = y2 - y1;

        
        document.querySelectorAll(".__vlm_bbox_overlay__")
            .forEach(e => e.remove());
        document.querySelectorAll(".__vlm_bbox_badge__")
            .forEach(e => e.remove());

        const box = document.createElement("div");
        box.className = "__vlm_bbox_overlay__";
        box.style.position = "fixed";
        box.style.left = x1 + "px";
        box.style.top = y1 + "px";
        box.style.width = width + "px";
        box.style.height = height + "px";
        box.style.border = "4px solid " + color;
        box.style.boxShadow = `0 0 0 4px ${color}55, 0 0 15px ${color}`;
        box.style.background = color + "22";
        box.style.zIndex = "2147483647";
        box.style.pointerEvents = "none";
        box.style.borderRadius = "6px";

        document.body.appendChild(box);

        const badge = document.createElement("div");
        badge.className = "__vlm_bbox_badge__";
        badge.innerText = labelText;

        badge.style.position = "fixed";
        badge.style.left = x1 + "px";
        badge.style.top = (y1 - 28) + "px";
        badge.style.background = color;
        badge.style.color = "white";
        badge.style.padding = "4px 8px";
        badge.style.fontSize = "12px";
        badge.style.fontWeight = "bold";
        badge.style.borderRadius = "6px";
        badge.style.zIndex = "2147483647";
        badge.style.pointerEvents = "none";
        badge.style.boxShadow = "0 2px 6px rgba(0,0,0,0.3)";

        document.body.appendChild(badge);

        box.animate([
            { transform: "scale(1)" },
            { transform: "scale(1.02)" },
            { transform: "scale(1)" }
        ], {
            duration: 600,
            iterations: Infinity
        });

    """, x1, y1, x2, y2, color, label)
def highlight_bbox_ani(driver, bbox, *,
                   color="#ff0066",
                   label="SELECTED ELEMENT"):

    x1, y1, x2, y2 = bbox

    driver.execute_script("""
        const x1 = arguments[0];
        const y1 = arguments[1];
        const x2 = arguments[2];
        const y2 = arguments[3];
        const color = arguments[4];
        const labelText = arguments[5];

        const width = x2 - x1;
        const height = y2 - y1;

        
        document.querySelectorAll(".__vlm_bbox_overlay__")
            .forEach(e => e.remove());
        document.querySelectorAll(".__vlm_bbox_badge__")
            .forEach(e => e.remove());

        const box = document.createElement("div");
        box.className = "__vlm_bbox_overlay__";
        box.style.position = "fixed";
        box.style.left = x1 + "px";
        box.style.top = y1 + "px";
        box.style.width = width + "px";
        box.style.height = height + "px";
        box.style.border = "4px solid " + color;
        box.style.boxShadow = `0 0 0 4px ${color}55, 0 0 15px ${color}`;
        box.style.background = color + "22";
        box.style.zIndex = "2147483647";
        box.style.pointerEvents = "none";
        box.style.borderRadius = "6px";

        document.body.appendChild(box);

    """, x1, y1, x2, y2, color, label)
def highlight_bbox(driver, bbox,
                   color="255, 0, 102",
                   alpha=0.25,
                   border_px=1):
    x1, y1, x2, y2 = bbox

    driver.execute_script("""
        const x1 = arguments[0];
        const y1 = arguments[1];
        const x2 = arguments[2];
        const y2 = arguments[3];
        const rgb = arguments[4];
        const alpha = arguments[5];
        const borderPx = arguments[6];

        const width = x2 - x1;
        const height = y2 - y1;

        document.querySelectorAll(".__vlm_soft_overlay__")
            .forEach(e => e.remove());

        const overlay = document.createElement("div");
        overlay.className = "__vlm_soft_overlay__";

        overlay.style.position = "fixed";
        overlay.style.left = x1 + "px";
        overlay.style.top = y1 + "px";
        overlay.style.width = width + "px";
        overlay.style.height = height + "px";

        overlay.style.background = `rgba(${rgb}, ${alpha})`;

        overlay.style.border = borderPx + "px solid rgb(" + rgb + ")";

        overlay.style.zIndex = 2147483647;
        overlay.style.pointerEvents = "none";
        overlay.style.borderRadius = "6px";
        overlay.style.boxSizing = "border-box";

        document.documentElement.appendChild(overlay);
    """, x1, y1, x2, y2, color, alpha, border_px)

def highlight_bbox_focus(driver, bbox):

    x1, y1, x2, y2 = bbox

    driver.execute_script("""
        const x1 = arguments[0];
        const y1 = arguments[1];
        const x2 = arguments[2];
        const y2 = arguments[3];

        const vw = window.innerWidth;
        const vh = window.innerHeight;

        const width = x2 - x1;
        const height = y2 - y1;

        
        document.querySelectorAll(".__vlm_dim__")
            .forEach(e => e.remove());
        document.querySelectorAll(".__vlm_box__")
            .forEach(e => e.remove());

        function makeDim(left, top, width, height) {
            const d = document.createElement("div");
            d.className = "__vlm_dim__";
            d.style.position = "fixed";
            d.style.left = left + "px";
            d.style.top = top + "px";
            d.style.width = width + "px";
            d.style.height = height + "px";
            d.style.background = "rgba(0,0,0,0.35)";
            d.style.zIndex = "2147483646";
            d.style.pointerEvents = "none";
            document.body.appendChild(d);
        }

        
        makeDim(0, 0, vw, y1);

        
        makeDim(0, y2, vw, vh - y2);

        
        makeDim(0, y1, x1, height);

        
        makeDim(x2, y1, vw - x2, height);

        const box = document.createElement("div");
        box.className = "__vlm_box__";
        box.style.position = "fixed";
        box.style.left = x1 + "px";
        box.style.top = y1 + "px";
        box.style.width = width + "px";
        box.style.height = height + "px";
        box.style.border = "1px solid #ff0066";
        box.style.borderRadius = "6px";
        box.style.zIndex = "2147483647";
        box.style.pointerEvents = "none";

        document.body.appendChild(box);

    """, x1, y1, x2, y2)

def unhighlight_bbox(driver):
    driver.execute_script("""
        document.querySelectorAll(".__vlm_bbox_overlay__")
            .forEach(e => e.remove());
        document.querySelectorAll(".__vlm_bbox_badge__")
            .forEach(e => e.remove());
    """)

def clear_all_visual_debug(driver):
    driver.execute_script("""
        const overlaySelectors = [
            ".__vlm_bbox_overlay__",
            ".__vlm_bbox_label__",
            ".__vlm_highlight_overlay__",
            ".__vlm_highlight_badge__",
            "#__click_debug_marker__",
            "#__crosshair_h__",
            "#__crosshair_v__"
        ];

        overlaySelectors.forEach(sel => {
            document.querySelectorAll(sel)
                .forEach(e => e.remove());
        });

        document.querySelectorAll("[data-_orig_style]")
            .forEach(el => {
                const orig = el.getAttribute("data-_orig_style");
                el.setAttribute("style", orig);
                el.removeAttribute("data-_orig_style");
            });
    """)

def show_click_point(driver, point, size=8, color="red"):
    """
    point: [x, y]
    """

    x, y = point

    driver.execute_script("""
        const x = arguments[0];
        const y = arguments[1];
        const size = arguments[2];
        const color = arguments[3];

        const h = document.createElement("div");
        const v = document.createElement("div");

        h.id = "__crosshair_h__";
        v.id = "__crosshair_v__";

        h.style.position = v.style.position = "fixed";
        h.style.backgroundColor = v.style.backgroundColor = color;
        h.style.zIndex = v.style.zIndex = 999999;
        h.style.pointerEvents = v.style.pointerEvents = "none";

        h.style.left = (x - size) + "px";
        h.style.top = y + "px";
        h.style.width = (size * 2) + "px";
        h.style.height = "2px";

        v.style.left = x + "px";
        v.style.top = (y - size) + "px";
        v.style.width = "2px";
        v.style.height = (size * 2) + "px";

        document.body.appendChild(h);
        document.body.appendChild(v);
    """, x, y, size, color)

def remove_click_point(driver):
    driver.execute_script("""
        const marker = document.getElementById("__click_debug_marker__");
        if (marker) {
            marker.remove();
        }
    """)

# import components.credential_fetcher as cf
import components.external_handlers as eh
from importlib import reload
reload(eh)
import time
from selenium.webdriver.support.ui import WebDriverWait

def get_click_point_from_element(driver, element, *, use_center=True):
    """
    element: Selenium WebElement
    """

    rect = driver.execute_script("""
        const r = arguments[0].getBoundingClientRect();
        return {
            left: r.left,
            top: r.top,
            width: r.width,
            height: r.height
        };
    """, element)

    if use_center:
        x = rect["left"] + rect["width"] / 2
        y = rect["top"] + rect["height"] / 2
    else:
        x = rect["left"] + rect["width"] / 2
        y = rect["top"] + rect["height"] * 0.3

    return [float(x), float(y)]

def get_click_point_from_bbox(bbox, *, use_center=True):
    """
    return: [x, y]
    """

    x1, y1, x2, y2 = map(float, bbox)

    w = x2 - x1
    h = y2 - y1

    if use_center:
        x = x1 + w / 2
        y = y1 + h / 2
    else:
        x = x1 + w / 2
        y = y1 + h * 0.3

    return [x, y]

def has_password_action(action_list):
    for item in action_list:
        action = item.get("action", item)
        if action.get("action_type") == "type" and action.get("kind") == "input":
            hint = (action.get("text_hint") or "").lower()
            rationale = (action.get("rationale") or "").lower()
            if "password" in hint or "password" in rationale:
                return False
    return True

def execute_action_by_point(driver, node, click_point, value, domain, cand_actions=None):
    if node.action["action_type"] == "click":
        old_handles = driver.window_handles.copy()
        safe_click_by_point(driver, click_point)

        try:
            WebDriverWait(driver, 5).until(
                lambda d: len(d.window_handles) > len(old_handles)
            )
        except:
            pass

        if len(driver.window_handles) > len(old_handles):
            new_tab = list(set(driver.window_handles) - set(old_handles))[0]
            driver.switch_to.window(new_tab)

    elif node.action["action_type"] == "type":
        if value == "email_retriever":
            value = eh.get_email(domain)[0]

            type_by_point(driver, click_point, value, press_enter=has_password_action(cand_actions))
        elif value == "password_retriever":
            value = eh.get_password(domain)[0]
            type_by_point(driver, click_point, value, press_enter=True)
        elif value == "2FA_retriever":
            value = eh.get_2FA_code()
            type_by_point(driver, click_point, value, press_enter=True)
        else:
            type_by_point(driver, click_point, value, press_enter=True)

    time.sleep(6)

def execute_action_by_point_replay(driver, action_type, click_point, value, domain):

    if action_type == "click":
        old_handles = driver.window_handles.copy()
        safe_click_by_point(driver, click_point)

        try:
            WebDriverWait(driver, 5).until(
                lambda d: len(d.window_handles) > len(old_handles)
            )
        except:
            pass

        if len(driver.window_handles) > len(old_handles):
            new_tab = list(set(driver.window_handles) - set(old_handles))[0]
            driver.switch_to.window(new_tab)

    elif action_type == "type":
        if value == "email_retriever":
            value = eh.get_email(domain)[0]
        elif value == "password_retriever":
            value = eh.get_password(domain)[0]
        elif value == "2FA_retriever":
            value = eh.get_2FA_code()

        type_by_point(driver, click_point, value, press_enter=False)

    time.sleep(6)

_JS_GET_SCROLLABLE = """
    function getScrollable() {
        const win = document.scrollingElement || document.documentElement;
        if (win.scrollHeight > win.clientHeight + 10)
            return win;

        const els = Array.from(document.querySelectorAll('*'));
        let best = null, bestArea = 0;
        for (const el of els) {
            const style = getComputedStyle(el);
            if ((style.overflowY === 'auto' || style.overflowY === 'scroll')
                    && el.scrollHeight > el.clientHeight + 10) {
                const r = el.getBoundingClientRect();
                const area = r.width * r.height;
                if (area > bestArea) { bestArea = area; best = el; }
            }
        }
        return best || win;
    }
"""

def scroll(driver):
    driver.execute_script(_JS_GET_SCROLLABLE + """
        const sc = getScrollable();
        sc.scrollTo(0, sc.scrollTop + sc.clientHeight);
    """)

def chk_bottom(driver, threshold_px: int = 150):
    return driver.execute_script(_JS_GET_SCROLLABLE + """
        const sc = getScrollable();
        return sc.scrollTop + sc.clientHeight >= sc.scrollHeight - arguments[0];
    """, threshold_px)

def get_viewport_info(driver) -> str:
    return driver.execute_script(_JS_GET_SCROLLABLE + """
        const sc           = getScrollable();
        const scrollTop    = sc.scrollTop;
        const clientH      = sc.clientHeight;
        const totalH       = sc.scrollHeight;
        const coverage     = Math.round((clientH / totalH) * 100);
        const topPercent   = Math.round((scrollTop / totalH) * 100);
        const bottomPercent = Math.min(topPercent + coverage, 100);
        return `Viewing ${topPercent}%-${bottomPercent}% of page (${coverage}% visible)`;
    """)

from selenium.webdriver.common.by import By
from urllib.parse import urljoin
import re

def _build_py_patterns(texts):
    pats = []
    for kw in texts:
        words   = kw.strip().split()
        escaped = [re.escape(w) for w in words]
        inner   = r'\s*'.join(escaped)
        has_cjk = any(ord(c) > 0x2E7F for c in kw)
        if len(words) > 1 or has_cjk:
            pats.append(re.compile(inner, re.IGNORECASE))
        else:
            pats.append(re.compile(
                r'(?<![a-zA-Z0-9])' + inner + r'(?![a-zA-Z0-9])',
                re.IGNORECASE
            ))
    return pats

def _matches_any_py(val: str, patterns: list[re.Pattern]) -> bool:
    if not val:
        return False
    return any(p.search(val) for p in patterns)

_JS_KW_HELPERS = r"""
    const _CHECK_ATTRS = [
        "href","aria-label","title","id","name",
        "data-testid","data-test","data-qa","data-cy",
        "role","placeholder","alt"
    ];

    // JS
    function buildPatterns(keywords) {
        return keywords.map(function(kw) {
            var words   = kw.trim().split(/\s+/);
            var escaped = words.map(function(w) {
                return w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
            });
            var inner  = escaped.join('\\s*');
            var hasCJK = /[\u2E80-\u9FFF\uAC00-\uD7AF]/.test(kw);
            if (words.length > 1 || hasCJK) {
                return new RegExp(inner, 'i');
            } else {
                return new RegExp('(?<![a-zA-Z0-9])' + inner + '(?![a-zA-Z0-9])', 'i');
            }
        });
    }

    function matchesAny(val, patterns) {
        if (!val || !patterns) return false;
        for (var i = 0; i < patterns.length; i++) {
            if (patterns[i].test(val)) return true;
        }
        return false;
    }

    function checkElKeyword(el, patterns) {
        var text = (el.innerText || el.textContent || "").trim();
        if (matchesAny(text, patterns)) return true;
        for (var i = 0; i < _CHECK_ATTRS.length; i++) {
            var v = el.getAttribute && el.getAttribute(_CHECK_ATTRS[i]);
            if (matchesAny(v, patterns)) return true;
        }
        return false;
    }
"""

_JS_NAV_HELPERS = r"""
    const NAV_ROLES    = new Set(["navigation","menubar","menu","tablist"]);
    const NAV_KEYWORDS = /nav|menu|header|topbar|navbar|top-bar|main-menu|site-nav/i;

    const FOOTER_ROLES    = new Set(["contentinfo"]);
    const FOOTER_KEYWORDS = /footer|foot-nav|bottom-bar|footnote|foot_nav/i;
    const BREADCRUMB_KW   = /breadcrumb|crumb/i;

    function isSelfOrAncestorFooter(el) {
        var cur = el;
        while (cur && cur !== document.documentElement) {
            var tag  = (cur.tagName || "").toLowerCase();
            var role = (cur.getAttribute && cur.getAttribute("role") || "").toLowerCase();
            var id   = (cur.getAttribute && cur.getAttribute("id"))    || "";
            var cls  = (cur.getAttribute && cur.getAttribute("class")) || "";
            var al   = (cur.getAttribute && cur.getAttribute("aria-label")) || "";
            if (tag === "footer")                                        return true;
            if (FOOTER_ROLES.has(role))                                  return true;
            if (FOOTER_KEYWORDS.test(id) || FOOTER_KEYWORDS.test(cls))  return true;
            if (FOOTER_KEYWORDS.test(al))                                return true;
            cur = cur.parentElement;
        }
        return false;
    }

    function isBreadcrumb(el) {
        var al  = (el.getAttribute && el.getAttribute("aria-label") || "");
        var id  = (el.getAttribute && el.getAttribute("id"))    || "";
        var cls = (el.getAttribute && el.getAttribute("class")) || "";
        return BREADCRUMB_KW.test(al) || BREADCRUMB_KW.test(id) || BREADCRUMB_KW.test(cls);
    }

    function isNavContainer(el, topRatio, vh) {
        if (isSelfOrAncestorFooter(el)) return false;
        if (isBreadcrumb(el))           return false;

        var tag  = (el.tagName || "").toLowerCase();
        var role = (el.getAttribute && el.getAttribute("role") || "").toLowerCase();

        if (tag === "nav")         return true;
        if (NAV_ROLES.has(role))   return true;

        var id  = (el.getAttribute && el.getAttribute("id"))    || "";
        var cls = (el.getAttribute && el.getAttribute("class")) || "";
        if (!NAV_KEYWORDS.test(id) && !NAV_KEYWORDS.test(cls)) return false;

        var r = el.getBoundingClientRect();
        if (r.top    > vh * topRatio) return false;
        if (r.bottom > vh * 0.9)      return false;

        return el.querySelectorAll("a[href]").length >= 2;
    }

    function isLooseClickable(el) {
        var tag  = (el.tagName || "").toLowerCase();
        var role = el.getAttribute && el.getAttribute("role");

        if (tag === "button") return true;
        if (tag === "a" && el.getAttribute("href")) return true;
        if (tag === "label" && el.getAttribute("for")) return true;
        if (tag === "input") {
            var t = (el.getAttribute("type") || "").toLowerCase();
            return ["button","submit","reset","image"].indexOf(t) !== -1;
        }
        if (role && ["button","link","menuitem","tab","option"].indexOf(role) !== -1)
            return true;

        var tabindex = el.getAttribute && el.getAttribute("tabindex");
        if (tabindex !== null && !isNaN(parseInt(tabindex)) && parseInt(tabindex) >= 0)
            return true;

        if (el.onclick || el.onmousedown || el.onmouseup) return true;

        try { if (getComputedStyle(el).cursor === "pointer") return true; } catch(e){}

        if (el.hasAttribute("data-testid") || el.hasAttribute("data-test") ||
            el.hasAttribute("data-qa")     || el.hasAttribute("data-cy")   ||
            el.hasAttribute("data-action")) return true;

        var ap = el.getAttribute && el.getAttribute("aria-pressed");
        var ae = el.getAttribute && el.getAttribute("aria-expanded");
        if (ap !== null || ae !== null) return true;

        if (role && role !== "presentation" && role !== "none") return true;

        return false;
    }

    function makeNavItem(el, chain, kind, navGroup) {
        var r = el.getBoundingClientRect();
        return {
            _reason:       "nav",
            bbox:          [r.left, r.top, r.right, r.bottom],
            overlap_area:  0,
            tag:           (el.tagName || "").toLowerCase(),
            kind:          kind,
            role:          (el.getAttribute && el.getAttribute("role")) || "",
            text:          getText(el),
            attrs:         pickAttrs(el),
            type:          (el.getAttribute && el.getAttribute("type")) || "",
            el:            el,
            _shadow_chain: (chain && chain.length) ? chain : null,
            nav_group:     navGroup,
        };
    }

    function makeKwItem(el, chain) {
        var r = el.getBoundingClientRect();
        return {
            _reason:       "kw_match",
            bbox:          [r.left, r.top, r.right, r.bottom],
            overlap_area:  0,
            tag:           (el.tagName || "").toLowerCase(),
            kind:          "kw_match",
            role:          (el.getAttribute && el.getAttribute("role")) || "",
            text:          getText(el),
            attrs:         pickAttrs(el),
            type:          (el.getAttribute && el.getAttribute("type")) || "",
            el:            el,
            _shadow_chain: (chain && chain.length) ? chain : null,
            nav_group:     null,
        };
    }
"""

def collect_nav_and_keyword_elements(
    driver,
    keywords: list[dict],
    *,
    max_results_nav: int = 200,
    max_results_kw: int = 500,
    top_ratio: float = 0.35,
) -> list[dict]:
    """

    kind:
    """
    keyword_texts = [k["text"] for k in keywords]

    _JS = _JS_COMMON + _JS_KW_HELPERS + _JS_NAV_HELPERS + r"""
        var _maxResultsNav = arguments[0];
        var _maxResultsKw  = arguments[1];
        var _topRatio      = arguments[2];
        var _keywords      = arguments[3];

        var _patterns = buildPatterns(_keywords);
        var vh        = window.innerHeight;
        var all       = collectAllWithShadow(document, []);

        var navResults = [];
        var kwResults  = [];
        var seenEls    = new Set();
        var navHrefs   = new Set();

        // pass 1: nav containers → clickables
        for (var i = 0; i < all.length; i++) {
            var el    = all[i].el;
            var chain = all[i].chain;
            if (!el || !el.getBoundingClientRect) continue;
            if (!isNavContainer(el, _topRatio, vh)) continue;
            if (!isVisible(el)) continue;
            if (seenEls.has(el)) continue;
            seenEls.add(el);

            var groupId = (
                (el.getAttribute("id") || el.getAttribute("class") || el.tagName)
            ).slice(0, 60);

            var walker = (el.ownerDocument || document).createTreeWalker(
                el, NodeFilter.SHOW_ELEMENT
            );
            var node = walker.nextNode();
            while (node) {
                if (isVisible(node) && isLooseClickable(node) && !seenEls.has(node)) {
                    navResults.push(makeNavItem(node, chain, "nav_clickable", groupId));
                    seenEls.add(node);
                    var h = node.getAttribute && node.getAttribute("href");
                    if (h) navHrefs.add(h);
                }
                node = walker.nextNode();
                if (navResults.length >= _maxResultsNav) break;
            }
            if (navResults.length >= _maxResultsNav) break;
        }

        for (var j = 0; j < all.length; j++) {
            var el2    = all[j].el;
            var chain2 = all[j].chain;
            if (!el2 || !el2.getAttribute) continue;
            if (seenEls.has(el2)) continue;
            if (!isLooseClickable(el2)) continue;
            if (!isVisible(el2)) continue;
            if (!checkElKeyword(el2, _patterns)) continue;
            var href2 = el2.getAttribute("href");
            if (href2 && navHrefs.has(href2)) continue;
            kwResults.push(makeKwItem(el2, chain2));
            if (kwResults.length >= _maxResultsKw) break;
        }

        return { nav: navResults, kw: kwResults };
    """

    def _js_collect(frame_path, fr_offset=(0.0, 0.0), frame_bbox_top=None):
        dx, dy = fr_offset
        raw = driver.execute_script(
            _JS, max_results_nav, max_results_kw, top_ratio, keyword_texts
        ) or {}

        nav_items, kw_items = [], []

        for r in (raw.get("nav") or []):
            b = r["bbox"]
            nav_items.append({
                **r,
                "bbox":            [b[0]+dx, b[1]+dy, b[2]+dx, b[3]+dy],
                "_frame_path":     frame_path[:],
                "_frame_bbox_top": frame_bbox_top,
                "_cross_origin":   False,
            })

        for r in (raw.get("kw") or []):
            b = r["bbox"]
            kw_items.append({
                **r,
                "bbox":            [b[0]+dx, b[1]+dy, b[2]+dx, b[3]+dy],
                "_frame_path":     frame_path[:],
                "_frame_bbox_top": frame_bbox_top,
                "_cross_origin":   False,
            })

        return nav_items, kw_items

    def _walk_frames(depth=0, max_depth=2):
        if depth >= max_depth:
            return [], []
        nav_acc, kw_acc = [], []
        frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
        for i, fr in enumerate(frames):
            entered = False
            try:
                fr_rect = driver.execute_script(
                    "const r=arguments[0].getBoundingClientRect();"
                    "return {left:r.left,top:r.top,right:r.right,bottom:r.bottom,"
                    "        width:r.width,height:r.height};", fr
                )
                if not fr_rect or fr_rect["width"] <= 0 or fr_rect["height"] <= 0:
                    continue
                fr_offset      = (fr_rect["left"], fr_rect["top"])
                frame_bbox_top = [fr_rect["left"], fr_rect["top"],
                                  fr_rect["right"], fr_rect["bottom"]]
                driver.switch_to.frame(fr)
                entered = True
                try:
                    n, k = _js_collect(["top", i], fr_offset, frame_bbox_top)
                    nav_acc.extend(n)
                    kw_acc.extend(k)
                    rn, rk = _walk_frames(depth + 1)
                    nav_acc.extend(rn)
                    kw_acc.extend(rk)
                except Exception:
                    pass
                driver.switch_to.parent_frame()
                entered = False
            except Exception:
                if entered:
                    try:
                        driver.switch_to.parent_frame()
                    except Exception:
                        pass
        return nav_acc, kw_acc

    driver.switch_to.default_content()
    nav_items, kw_items = _js_collect(["top"])
    rn, rk = _walk_frames()
    nav_items.extend(rn)
    kw_items.extend(rk)
    driver.switch_to.default_content()

    nav_hrefs_all = {
        (it.get("attrs") or {}).get("href")
        for it in nav_items
        if (it.get("attrs") or {}).get("href")
    }
    kw_items = [
        it for it in kw_items
        if (it.get("attrs") or {}).get("href") not in nav_hrefs_all
    ]

    seen_key: set[tuple] = set()
    deduped_kw = []
    for it in kw_items:
        key = (it.get("tag", ""), (it.get("text") or "").strip(),
               (it.get("attrs") or {}).get("href", ""))
        if key in seen_key:
            continue
        seen_key.add(key)
        deduped_kw.append(it)

    return nav_items + deduped_kw

def collect_keyword_elements(
    driver,
    keywords: list[dict],
    *,
    max_results: int = 500,
) -> list[dict]:
    """

    kind: "kw_match"
    """
    keyword_texts = [k["text"] for k in keywords]

    _JS = _JS_COMMON + _JS_KW_HELPERS + _JS_NAV_HELPERS + r"""
        var _maxResults = arguments[0];
        var _keywords   = arguments[1];

        var _patterns = buildPatterns(_keywords);
        var all       = collectAllWithShadow(document, []);
        var results   = [];

        for (var i = 0; i < all.length; i++) {
            var el    = all[i].el;
            var chain = all[i].chain;
            if (!el || !el.getAttribute) continue;
            if (!isLooseClickable(el)) continue;
            if (!isVisible(el)) continue;

            var _tag = (el.tagName || "").toLowerCase();
            if (_tag === "a") {
                if (!el.getAttribute("href")) continue;
                if ((el.getAttribute("role") || "").toLowerCase() === "button") continue;
            }

            if (!checkElKeyword(el, _patterns)) continue;
            results.push(makeKwItem(el, chain));
            if (results.length >= _maxResults) break;
        }
        return results;
    """

    def _js_collect(frame_path, fr_offset=(0.0, 0.0), frame_bbox_top=None):
        dx, dy = fr_offset
        raws = driver.execute_script(_JS, max_results, keyword_texts) or []
        items = []
        for r in raws:
            b = r["bbox"]
            items.append({
                **r,
                "bbox":            [b[0]+dx, b[1]+dy, b[2]+dx, b[3]+dy],
                "_frame_path":     frame_path[:],
                "_frame_bbox_top": frame_bbox_top,
                "_cross_origin":   False,
            })
        return items

    def _walk_frames(depth=0, max_depth=2):
        if depth >= max_depth:
            return []
        items = []
        frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
        for i, fr in enumerate(frames):
            entered = False
            try:
                fr_rect = driver.execute_script(
                    "const r=arguments[0].getBoundingClientRect();"
                    "return {left:r.left,top:r.top,right:r.right,bottom:r.bottom,"
                    "        width:r.width,height:r.height};", fr
                )
                if not fr_rect or fr_rect["width"] <= 0 or fr_rect["height"] <= 0:
                    continue
                fr_offset      = (fr_rect["left"], fr_rect["top"])
                frame_bbox_top = [fr_rect["left"], fr_rect["top"],
                                  fr_rect["right"], fr_rect["bottom"]]
                driver.switch_to.frame(fr)
                entered = True
                try:
                    items.extend(_js_collect(["top", i], fr_offset, frame_bbox_top))
                    items.extend(_walk_frames(depth + 1))
                except Exception:
                    pass
                driver.switch_to.parent_frame()
                entered = False
            except Exception:
                if entered:
                    try:
                        driver.switch_to.parent_frame()
                    except Exception:
                        pass
        return items

    _JS_TEXT_FALLBACK = _JS_COMMON + r"""
        var _keywords = arguments[0];
        var kwSet     = new Set(_keywords);

        function isVisible(el) {
            var st = getComputedStyle(el);
            if (!st) return false;
            if (st.visibility === "hidden" || st.display === "none" ||
                parseFloat(st.opacity || "1") === 0) return false;

            var r = el.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0) return false;

            
            var cur = el;
            while (cur && cur !== document.body) {
                if (cur.getAttribute("aria-hidden") === "true") return false;
                if (cur.hasAttribute("inert")) return false;
                cur = cur.parentElement;
            }

            return true;
        }

        var all     = collectAllWithShadow(document, []);
        var results = [];

        for (var i = 0; i < all.length; i++) {
            var el    = all[i].el;
            var chain = all[i].chain;
            if (!el) continue;
            var text = (el.innerText || "").trim();
            if (!text) continue;
            if (!kwSet.has(text)) continue;
            if (!isVisible(el)) continue;

            var tag = (el.tagName || "").toLowerCase();
            if (tag === "a" && !el.getAttribute("href")) continue;
            var r2 = el.getBoundingClientRect();
            var cx = r2.left + r2.width  / 2;
            var cy = r2.top  + r2.height / 2;
            if (cx >= 0 && cy >= 0 && cx <= window.innerWidth && cy <= window.innerHeight) {
                function _deepFromPoint(x, y, root, depth) {
                    if (!depth) depth = 0;
                    var f = root.elementFromPoint(x, y);
                    if (!f) return null;
                    if (f.shadowRoot) return _deepFromPoint(x, y, f.shadowRoot, depth + 1);
                    return f;
                }
                var topEl = _deepFromPoint(cx, cy, document, 0);
                if (topEl && el !== topEl && !el.contains(topEl)) continue;
            }

            var r = el.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0) continue;
            results.push({
                _reason:       "kw_text_fallback",
                bbox:          [r.left, r.top, r.right, r.bottom],
                overlap_area:  0,
                tag:           (el.tagName || "").toLowerCase(),
                kind:          "kw_match",
                role:          (el.getAttribute && el.getAttribute("role")) || "",
                text:          text,
                attrs:         pickAttrs(el),
                type:          "",
                el:            el,
                _shadow_chain: (chain && chain.length) ? chain : null,
                nav_group:     null,
            });
        }
        return results;
    """

    def _js_text_fallback(frame_path, fr_offset=(0.0, 0.0), frame_bbox_top=None):
        dx, dy = fr_offset
        raws = driver.execute_script(_JS_TEXT_FALLBACK, keyword_texts) or []
        items = []
        for r in raws:
            b = r["bbox"]
            items.append({
                **r,
                "bbox":            [b[0]+dx, b[1]+dy, b[2]+dx, b[3]+dy],
                "_frame_path":     frame_path[:],
                "_frame_bbox_top": frame_bbox_top,
                "_cross_origin":   False,
            })
        return items

    def _walk_frames_fallback(depth=0, max_depth=2):
        if depth >= max_depth:
            return []
        items = []
        frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
        for i, fr in enumerate(frames):
            entered = False
            try:
                fr_rect = driver.execute_script(
                    "const r=arguments[0].getBoundingClientRect();"
                    "return {left:r.left,top:r.top,right:r.right,bottom:r.bottom,"
                    "        width:r.width,height:r.height};", fr
                )
                if not fr_rect or fr_rect["width"] <= 0 or fr_rect["height"] <= 0:
                    continue
                fr_offset      = (fr_rect["left"], fr_rect["top"])
                frame_bbox_top = [fr_rect["left"], fr_rect["top"],
                                  fr_rect["right"], fr_rect["bottom"]]
                driver.switch_to.frame(fr)
                entered = True
                try:
                    items.extend(_js_text_fallback(["top", i], fr_offset, frame_bbox_top))
                    items.extend(_walk_frames_fallback(depth + 1))
                except Exception:
                    pass
                driver.switch_to.parent_frame()
                entered = False
            except Exception:
                if entered:
                    try:
                        driver.switch_to.parent_frame()
                    except Exception:
                        pass
        return items

    driver.switch_to.default_content()
    items = _js_collect(["top"])
    items.extend(_walk_frames())

    fallback = _js_text_fallback(["top"])
    fallback.extend(_walk_frames_fallback())
    driver.switch_to.default_content()

    seen_key: set[tuple] = set()
    deduped = []

    def _item_key(it):
        attrs = it.get("attrs") or {}
        b     = it.get("bbox") or [0,0,0,0]
        text  = (it.get("text") or "").strip()
        has_id = attrs.get("data-qa") or attrs.get("aria-label") or attrs.get("id")
        href  = attrs.get("href", "")
        return (
            it.get("tag", ""),
            text,
            href,
            attrs.get("data-qa", ""),
            attrs.get("aria-label", ""),
            attrs.get("id", ""),
            round((b[0]+b[2])/2) if not has_id else "",
            round((b[1]+b[3])/2) if not has_id else "",
    )

    for it in items + fallback:
        key = _item_key(it)
        if key in seen_key:
            continue
        seen_key.add(key)
        deduped.append(it)

    return deduped

def format_nav_elements_for_prompt(driver, items: list[dict], keywords, *, refresh_bbox: bool=True) -> str:

    driver.switch_to.default_content()
    keyword_texts = [k["text"] for k in (keywords or [])]

    """

      [1] [button] "Open menu" | aria-label="Menu" | bbox=[16,16,48,48]
      [2] [a] "Sign in" | href="/login" | bbox=[100,10,160,40]
    """
    _KEEP_TAGS  = {"button", "a", "input", "select", "textarea",  "div"}
    _SKIP_ATTRS = {"src", "tabindex", "autocomplete", "aria-labelledby"}
    _FOOTER_KW  = re.compile(r'footer|contentinfo|foot-nav|bottom-bar', re.IGNORECASE)

    current_url = driver.execute_script(
        "return document.querySelector('base')?.href || window.location.href;"
    )
    vw, vh = driver.execute_script("return [window.innerWidth, window.innerHeight];")

    def _format_attrs(attrs: dict) -> list[str]:
        parts = []
        for k, v in (attrs or {}).items():
            if k in _SKIP_ATTRS or not v:
                continue
            if k == "href":
                v = urljoin(current_url, v)
                parts.append(f'{k}="{str(v)[:60]}"')
            else:
                parts.append(f'{k}="{str(v)[:60]}"')
        return parts

    def _is_footer(it: dict) -> bool:
        if _FOOTER_KW.search(it.get("role") or ""):          return True
        if _FOOTER_KW.search(it.get("nav_group") or ""):     return True
        if (it.get("text") or "").strip().lower().startswith("footer"): return True
        if _FOOTER_KW.search((it.get("attrs") or {}).get("aria-label") or ""): return True
        return False

    def _clean_text(t: str) -> str:
        return " ".join(t.split())

    def _is_strip_element(b: list, text: str) -> bool:
        w = b[2] - b[0]
        h = b[3] - b[1]
        if w <= 0 or h <= 0:
            return True
        if w >= vw * 0.8 and h < 200:
            return True
        if h >= vh * 0.8 and w < 200:
            return True
        return False

    _TAG_PRIORITY = {"button": 0, "a": 1, "input": 2, "label": 3,
                     "select": 4, "textarea": 5, "div": 6}

    candidates = []
    for it in items:
        kind = it.get("kind", "")
        if kind not in ("nav_clickable", "kw_match"):
            continue
        if _is_footer(it):
            continue
        tag = it.get("tag", "")
        if tag not in _KEEP_TAGS:
            continue

        b = it.get("bbox") or [0, 0, 0, 0]
        ctx = ""
        if refresh_bbox and it.get("el"):
            try:
                result = driver.execute_script(r"""
                    const el    = arguments[0];
                    const kwSet = new Set(arguments[1]);
                    const r     = el.getBoundingClientRect();

                    function getCtx(el, kwSet) {
                        const own = el.getAttribute("data-trackas") || el.getAttribute("data-lbl");
                        if (own) return own;

                        let cur = el.parentElement;
                        for (let d = 0; d < 8; d++) {
                            if (!cur || cur === document.body) break;
                            if ((cur.tagName||"").toLowerCase() === "tr") {
                                const cells = cur.querySelectorAll("td, th, label");
                                for (const cell of cells) {
                                    const t = (cell.innerText || "").trim();
                                    if (t && t !== (el.innerText||"").trim() && t.length < 50)
                                        return t;
                                }
                            }
                            cur = cur.parentElement;
                        }

                        cur = el.parentElement;
                        for (let d = 0; d < 8; d++) {
                            if (!cur || cur === document.body) break;
                            for (const child of cur.children) {
                                if (child === el || child.contains(el)) continue;
                                const t = (child.innerText || "").trim();
                                if (t && t.length < 80 && kwSet.has(t)) return t;
                            }
                            cur = cur.parentElement;
                        }

                        return null;
                    }

                    return {
                        bbox: [r.left, r.top, r.right, r.bottom],
                        ctx:  getCtx(el, kwSet),
                    };
                """, it["el"], keyword_texts)
                if result:
                    fresh = result.get("bbox")
                    if fresh and (fresh[2]-fresh[0]) > 1 and (fresh[3]-fresh[1]) > 1:
                        b = fresh
                    ctx = result.get("ctx") or ""
            except Exception as e:
                print(f"ERROR for {it.get('tag')} '{it.get('text','')[:20]}': {e}")

                pass

        if (b[2] - b[0]) <= 1 or (b[3] - b[1]) <= 1:
            continue

        el_area = (b[2]-b[0]) * (b[3]-b[1])
        if el_area > vw * vh * 0.4:
            continue

        if tag != "a":
            in_vp = b[1] < vh and b[3] > 0 and b[0] < vw and b[2] > 0
            if not in_vp:
                continue

        text = _clean_text(it.get("text") or "")

        if _is_strip_element(b, text):
            continue

        attr_parts = _format_attrs(it.get("attrs") or {})
        if not text and not attr_parts:
            continue

        candidates.append({
            **it,
            "bbox":        b,
            "_text":       text,
            "_attr_parts": attr_parts,
            "_attrs_raw":  dict(it.get("attrs") or {}),
            "_ctx":        ctx,
        })

    def _should_group(a, b):
        ix1 = max(a[0], b[0]); iy1 = max(a[1], b[1])
        ix2 = min(a[2], b[2]); iy2 = min(a[3], b[3])
        inter = max(0, ix2-ix1) * max(0, iy2-iy1)
        if inter == 0:
            return False
        area_a = max(1, (a[2]-a[0]) * (a[3]-a[1]))
        area_b = max(1, (b[2]-b[0]) * (b[3]-b[1]))
        return inter / min(area_a, area_b) >= 0.5

    groups: list[list[dict]] = []
    used = [False] * len(candidates)

    for i, it in enumerate(candidates):
        if used[i]:
            continue
        group = [it]
        used[i] = True
        for j in range(i+1, len(candidates)):
            if used[j]:
                continue
            if _should_group(it["bbox"], candidates[j]["bbox"]):
                group.append(candidates[j])
                used[j] = True
        groups.append(group)

    a_bboxes = [it["bbox"] for it in candidates if it.get("tag") == "a"]

    def _contained_in_any_a(b, self_it=None):
        for ab in a_bboxes:
            if self_it is not None:
                si_b = self_it.get("bbox") or []
                if (len(si_b) == 4 and
                    abs(ab[0]-si_b[0]) < 2 and abs(ab[1]-si_b[1]) < 2 and
                    abs(ab[2]-si_b[2]) < 2 and abs(ab[3]-si_b[3]) < 2):
                    continue
            ix1 = max(b[0], ab[0]); iy1 = max(b[1], ab[1])
            ix2 = min(b[2], ab[2]); iy2 = min(b[3], ab[3])
            inter = max(0, ix2-ix1) * max(0, iy2-iy1)
            area_b = max(1, (b[2]-b[0]) * (b[3]-b[1]))
            if inter / area_b >= 0.8:
                return True
        return False

    candidates = [
        it for it in candidates
        if not _contained_in_any_a(it["bbox"], self_it=it)
    ]

    merged = []

    def _is_container(it, others):
        b    = it["bbox"]
        area = max(1, (b[2]-b[0]) * (b[3]-b[1]))
        for other in others:
            ob         = other["bbox"]
            other_area = max(1, (ob[2]-ob[0]) * (ob[3]-ob[1]))
            if area > other_area * 3:
                return True
        return False

    for group in groups:
        merged_attrs: dict = {}
        for member in sorted(group, key=lambda x: _TAG_PRIORITY.get(x.get("tag",""), 99)):
            for k, v in (member.get("_attrs_raw") or {}).items():
                if k not in merged_attrs and v:
                    merged_attrs[k] = v

        if len(group) == 1:
            merged.append({
                **group[0],
                "_attr_parts": _format_attrs(merged_attrs),
            })
            continue

        non_containers = [
            it for it in group
            if not _is_container(it, [x for x in group if x is not it])
        ]
        targets = non_containers if non_containers else group

        text_best: dict[str, dict] = {}
        no_text = []
        for it in targets:
            t = it["_text"]
            if not t:
                no_text.append(it)
                continue
            pri = _TAG_PRIORITY.get(it.get("tag", ""), 99)
            if t not in text_best or pri < _TAG_PRIORITY.get(text_best[t].get("tag",""), 99):
                text_best[t] = it
        targets = list(text_best.values()) + no_text

        texts = [m["_text"] for m in group if m["_text"]]
        best_text = min(texts, key=len) if texts else ""

        for it in targets:
            merged.append({
                **it,
                "_text":       best_text,
                "_attr_parts": _format_attrs(merged_attrs),
            })

    seen_href: set[str] = set()
    final = []
    for it in merged:
        attrs = it.get("_attrs_raw") or {}
        href  = attrs.get("href", "")
        if href and href != "#" and not href.startswith("javascript:"):
            if href in seen_href:
                continue
            seen_href.add(href)
        final.append(it)

    lines = []
    idx   = 0
    for it in final:
        b          = it["bbox"]
        text       = it["_text"]
        attr_parts = it["_attr_parts"]
        ctx        = it.get("_ctx") or ""

        if not text and not attr_parts:
            continue

        idx += 1
        bbox_str = f"[{int(b[0])},{int(b[1])},{int(b[2])},{int(b[3])}]"
        content  = " | ".join(filter(None, [
            f'"{text}"' if text else None,
            f'context="{ctx}"' if ctx else None,
            *attr_parts,
            f"bbox={bbox_str}",
        ]))
        lines.append(f"[{idx}] [{it.get('tag','')}] {content}")

    return "\n".join(lines)

def find_matching_item(action: dict, items: list[dict]) -> dict | None:
    MAX_DIST = 10.0
    MIN_TEXT_SCORE = 0.8

    act = action.get("action", action)

    text_hint = (act.get("text_hint") or "").strip().lower()
    kind_hint = (act.get("kind") or "").strip().lower()
    bbox_hint = act.get("bbox")

    if not items:
        return None

    text_required = bool(text_hint)

    KIND_TO_TAG = {
        "button": {"button", "input"},
        "link":   {"a"},
        "input":  {"input", "textarea", "select"},
        "icon":   {"button", "a", "img", "svg"},
        "menu":   {"button", "a", "div", "li"},
    }
    expected_tags = KIND_TO_TAG.get(kind_hint, set())

    def _center(b):
        if not b or len(b) < 4:
            return None
        return ((b[0]+b[2])/2, (b[1]+b[3])/2)

    def _bbox_dist(b1, b2):
        c1, c2 = _center(b1), _center(b2)
        if not c1 or not c2:
            return float("inf")
        return ((c1[0]-c2[0])**2 + (c1[1]-c2[1])**2) ** 0.5

    def _href_matches(item_href: str, hint: str) -> bool:
        if not item_href or not hint:
            return False
        if item_href == hint:
            return True
        if item_href.startswith(hint):
            return True
        if hint.startswith(item_href):
            return True
        return False

    def _text_score(item: dict) -> float:
        item_text = (item.get("text") or "").strip().lower()
        item_href = (item.get("attrs") or {}).get("href", "")

        if not text_hint:
            return 0.5

        t = item_text
        if t == text_hint:               return 1.0
        if text_hint in t or t in text_hint: return 0.7
        a_tok = set(text_hint.split())
        b_tok = set(t.split())
        overlap = a_tok & b_tok
        ts = 0.4 * len(overlap) / max(len(a_tok), len(b_tok)) if overlap else 0.0

        if _href_matches(item_href, text_hint):
            return max(ts, 0.9)

        return ts

    def _kind_score(item: dict) -> float:
        if not expected_tags:
            return 0.0
        tag  = (item.get("tag") or "").lower()
        role = (item.get("role") or "").lower()
        if tag in expected_tags:
            return 1.0
        if role in {"button","link"} and kind_hint in ("button","link"):
            return 0.6
        return 0.0

    best_item  = None
    best_score = -1.0

    for it in items:
        item_text = (it.get("text") or "").strip().lower()

        ts = _text_score(item_text)

        if text_required and ts < MIN_TEXT_SCORE:
            continue

        if bbox_hint:
            dist = _bbox_dist(bbox_hint, it.get("bbox"))
            if dist > MAX_DIST:
                continue
            dist_score = max(0.0, 1.0 - dist / MAX_DIST)
        else:
            dist_score = 0.0

        ks = _kind_score(it)

        score = ts * 5.0 + ks * 2.0 + dist_score * 3.0

        if score > best_score:
            best_score = score
            best_item  = it

    return best_item

def find_matching_item(action: dict, items: list[dict]) -> dict | None:
    MAX_DIST = 10.0
    MIN_TEXT_SCORE = 0.8

    act = action.get("action", action)

    text_hint = " ".join((act.get("text_hint") or "").split()).lower()
    kind_hint = (act.get("kind") or "").strip().lower()
    bbox_hint = act.get("bbox")

    if not items:
        return None

    text_required = bool(text_hint)

    KIND_TO_TAG = {
        "button": {"button", "input"},
        "link":   {"a"},
        "input":  {"input", "textarea", "select"},
        "icon":   {"button", "a", "img", "svg"},
        "menu":   {"button", "a", "div", "li"},
    }
    expected_tags = KIND_TO_TAG.get(kind_hint, set())

    def _center(b):
        if not b or len(b) < 4:
            return None
        return ((b[0]+b[2])/2, (b[1]+b[3])/2)

    def _bbox_dist(b1, b2):
        c1, c2 = _center(b1), _center(b2)
        if not c1 or not c2:
            return float("inf")
        return ((c1[0]-c2[0])**2 + (c1[1]-c2[1])**2) ** 0.5

    def _text_score(item_text: str) -> float:
        if not text_hint:
            return 0.5
        if not item_text:
            return 0.0
        t = item_text.strip().lower()
        if t == text_hint:
            return 1.0
        if text_hint in t or t in text_hint:
            return 0.7
        a_tok = set(text_hint.split())
        b_tok = set(t.split())
        overlap = a_tok & b_tok
        if overlap:
            return 0.4 * len(overlap) / max(len(a_tok), len(b_tok))
        return 0.0

    def _kind_score(item: dict) -> float:
        if not expected_tags:
            return 0.0
        tag  = (item.get("tag") or "").lower()
        role = (item.get("role") or "").lower()
        if tag in expected_tags:
            return 1.0
        if role in {"button","link"} and kind_hint in ("button","link"):
            return 0.6
        return 0.0

    best_item  = None
    best_score = -1.0

    for it in items:
        item_text = " ".join((it.get("text") or "").split()).lower()

        ts = _text_score(item_text)

        if text_required and ts < MIN_TEXT_SCORE:
            continue

        if bbox_hint:
            dist = _bbox_dist(bbox_hint, it.get("bbox"))
            if dist > MAX_DIST:
                continue
            dist_score = max(0.0, 1.0 - dist / MAX_DIST)
        else:
            dist_score = 0.0

        ks = _kind_score(it)

        score = ts * 5.0 + ks * 2.0 + dist_score * 3.0

        if score > best_score:
            best_score = score
            best_item  = it

    return best_item

def test(driver, keywords):
    el = driver.find_element(By.CSS_SELECTOR, '[data-qa="user-button"]')
    keyword_texts = [k["text"] for k in keywords]

    result = driver.execute_script(
        _JS_COMMON + _JS_KW_HELPERS + _JS_NAV_HELPERS + r"""
        var _maxResults = arguments[0];
        var _keywords   = arguments[1];
        var _target     = arguments[2];

        var _patterns = buildPatterns(_keywords);
        var all       = collectAllWithShadow(document, []);
        var results   = [];
        var targetFound      = false;
        var targetFailReason = "not_in_all";

        for (var i = 0; i < all.length; i++) {
            var el    = all[i].el;
            var chain = all[i].chain;
            if (!el || !el.getAttribute) continue;

            if (el === _target) {
                targetFound = true;
                if (!isLooseClickable(el)) { targetFailReason = "isLooseClickable_false"; break; }
                if (!isVisible(el))        { targetFailReason = "isVisible_false"; break; }
                if (!checkElKeyword(el, _patterns)) { targetFailReason = "checkElKeyword_false"; break; }
                targetFailReason = "should_be_in_results";
                break;
            }

            if (!isLooseClickable(el)) continue;
            if (!isVisible(el)) continue;
            if (!checkElKeyword(el, _patterns)) continue;
            results.push(makeKwItem(el, chain));
            if (results.length >= _maxResults) {
                targetFailReason = "max_results_hit_before_target";
                break;
            }
        }

        return {
            targetFound:      targetFound,
            targetFailReason: targetFailReason,
            results_so_far:   results.length,
        };
    """, 500, keyword_texts, el)

    print(result)

from urllib.parse import urlparse
def is_navigable_href(href: str) -> bool:
    if not href or not href.strip():
        return False
    h = href.strip()
    if h.startswith('javascript:'):
        return False
    if h == '#' or h.startswith('#'):
        return False
    if h in ('void(0)', 'about:blank'):
        return False
    try:
        parsed = urlparse(h)
        if parsed.scheme and parsed.scheme not in ('http', 'https', 'ftp'):
            return False
        return bool(parsed.netloc or parsed.path)
    except Exception:
        return False