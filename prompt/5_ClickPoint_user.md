You are given:
1) A CROPPED screenshot
2) The bounding box of a VALIDATED clickable element (relative to the cropped image)
3) The intended ACTION

All coordinates refer to the cropped image pixel space. The top-left corner is (0, 0).

ACTION:
<<ACTION>>

VALIDATED_ELEMENT_BBOX:
<<BBOX>>

CROPPED_IMAGE_SIZE:
<<IMAGE_SIZE>>

--------------------------------------------------
OUTPUT FORMAT
--------------------------------------------------

{
  "click_point": [x, y]
}