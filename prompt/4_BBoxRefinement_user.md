You are given:
1) A CROPPED screenshot
2) CROPPED_IMAGE_SIZE
3) The intended ACTION

All coordinates refer to the cropped image pixel space. The top-left corner is (0, 0).

CROPPED_IMAGE_SIZE:
<<IMAGE_SIZE>>

ACTION:
<<ACTION>>


<<BBOX>>

--------------------------------------------------
OUTPUT FORMAT
--------------------------------------------------

{
  "match": "yes" | "no",
  "bbox": [x1, y1, x2, y2] | null,
  "observed_element_in_bbox": "one concise sentence describing what is visibly present inside SELECTED_ELEMENT_BBOX"
}

--------------------------------------------------
FIELD REQUIREMENTS
--------------------------------------------------

observed_element_in_bbox:
- MUST describe ONLY what is inside SELECTED_ELEMENT_BBOX.
- MUST NOT reference ACTION.
- MUST be exactly one concise sentence.
- If partial fragment, explicitly state that.

Rules:
- If match = "yes", bbox MUST be null.
- If match = "no", bbox MUST contain corrected coordinates or null.
- All coordinates MUST refer to cropped image pixel space.