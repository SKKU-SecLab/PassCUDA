import base64
import json
import components.element_utils as element_utils
from importlib import reload
reload(element_utils)
from components.element_utils import *

import time

from openai import OpenAI
import google.generativeai as genai

from transformers import AutoTokenizer

from enum import Enum

class Provider(str, Enum):
    VLLM = "vllm"
    OPENAI = "openai"
    GEMINI = "gemini"

import base64
from openai import OpenAI

class VLMClient:

    def __init__(self, provider, model, base_url=None, api_key=None, temperature=0):
        self.provider = provider
        self.model = model
        self.temperature = temperature

        if provider in ["vllm", "openai"]:
            self.client = OpenAI(
                base_url=base_url,
                api_key=api_key
            )

        elif provider == "gemini":
            genai.configure(api_key=api_key)
            self.client = genai.GenerativeModel(model)

        else:
            raise ValueError("Unsupported provider")

    def encode_image(self, path):
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def build_messages(self, user_prompt, image_b64=None, system_prompt=None):

        if self.provider in ["vllm", "openai"]:
            content = [{"type": "text", "text": user_prompt}]
            if image_b64:
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{image_b64}"
                    }
                })
            return [{"role": "system", "content": system_prompt}, {"role": "user", "content": content}]

    def generate(self, user_prompt, image_path=None, max_tokens=1024, system_prompt=None):

        image_b64 = None
        if image_path:
            image_b64 = self.encode_image(image_path)

        messages = self.build_messages(user_prompt, image_b64, system_prompt)

        if self.provider in ["vllm"]:

            if 'Qwen3.5' in self.model:
                think_resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=self.temperature,
                    max_tokens=512
                )
                reasoning_content = think_resp.choices[0].message.content
                reasoning_content = reasoning_content.replace("<think>", "").replace("</think>", "")

                messages.append({
                    "role": "assistant",
                    "content": f"<think>\n{reasoning_content}...omitted\n</think>\n\n"
                })

                tokenizer = AutoTokenizer.from_pretrained(self.model)
                prompt_ = tokenizer.apply_chat_template(
                    messages, tokenize=False, continue_final_message=True
                )

                resp = self.client.completions.create(
                    model=self.model,
                    prompt=prompt_,
                    temperature=self.temperature,
                    max_tokens=max_tokens
                )
                return resp, resp.choices[0].text
            else:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=self.temperature,
                    max_tokens=max_tokens
                )
                return resp, resp.choices[0].message.content

        elif self.provider in ["openai"]:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature
            )
            return resp, resp.choices[0].message.content

        elif self.provider == "gemini":

            parts = [user_prompt]

            if image_b64:
                parts.append({
                    "mime_type": "image/png",
                    "data": image_b64
                })

            model = genai.GenerativeModel(
                model_name=self.model_name,
                system_instruction=system_prompt if system_prompt else None
            )

            resp = model.generate_content(
                parts,
                generation_config={
                    "temperature": self.temperature,
                    "max_output_tokens": max_tokens,
                }
            )

            return resp, resp.text

    def generate_test(self, prompt, image_path=None, max_tokens=1024):

        image_b64 = None
        if image_path:
            image_b64 = self.encode_image(image_path)

        messages = self.build_messages(prompt, image_b64)

        if self.provider in ["vllm"]:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"}
            )
            return resp


def state_identifier(vlm, tree, image_path, current_node):
    node = tree.nodes[current_node]

    action_trace = str()
    for idx, action in enumerate(node.path_actions, 1):
        cleaned_action = {k: v for k, v in action.items() if k != "bbox"}
        action_trace += f"{idx}. {cleaned_action}\n"
    if action_trace == "":
        action_trace = "NULL"
    print(action_trace)

    with open("./prompt/1_StateIdentification_system.md", "r", encoding="utf-8") as f:
        system_prompt = f.read()

    with open("./prompt/1_StateIdentification_user.md", "r", encoding="utf-8") as f:
        user_prompt = f.read()

    user_prompt = user_prompt.replace(
        "<<ACTION_TRACE>>",
        action_trace
    )

    if current_node > 0:
        rationale = tree.nodes[current_node].action['rationale']
        user_prompt = user_prompt.replace(
            "<<ACTION_RATIONALE>>",
            rationale
        )

    raw_resp, state_resp = vlm.generate(user_prompt=user_prompt, image_path=image_path, system_prompt=system_prompt)

    return raw_resp, state_resp, system_prompt + '\n' + user_prompt


def action_generator_test(tree, current_node):
    disallowed_act = tree.get_disallowed_actions(current_node, tree.nodes[current_node].state)
    disallowed_act_str = json.dumps(disallowed_act, ensure_ascii=False, indent=2)
    print(disallowed_act_str)


def action_generator(vlm, tree, image_path, state, current_node, items_str, viewport_info):

    if state == 1:
        goal = "NAVIGATE to the LOGIN PAGE for the personal/individual account"
    elif state == 2:
        goal = "PERFORM LOGIN successfully using direct account credentials (email/username/phone + password), avoiding SSO unless unavoidable.\nAssume all required login credentials and 2FA verification codes can be entered directly via on-page input fields.\nDo not navigate to external services (e.g., email, SMS, help pages) to retrieve credentials or verification codes"
    elif state == 3:
        goal = "NAVIGATE to the PASSWORD CHANGE PAGE the PERSONAL/INDIVIDUAL account.\nThe navigation flow may involve:\n- profile or account pages\n- security or privacy pages\n- intermediate settings pages"
    elif state == 4:
        goal = "DISMISS or SKIP a LARGE BLOCKING ALERT OR INTERSTITIAL that interrupts or blocks the primary navigation flow"

    node = tree.nodes[current_node]

    action_trace = str()

    for idx, action in enumerate(node.path_actions, 1):
        action_wo_bbox = {k: v for k, v in action.items() if k != "bbox"}
        action_trace += f"{idx}. {json.dumps(action_wo_bbox, ensure_ascii=False)}\n"

    if action_trace == "":
        action_trace = "NULL"

    with open("./prompt/2_ActionGeneration_system.md", "r", encoding="utf-8") as f:
        system_prompt = f.read()

    with open("./prompt/2_ActionGeneration_user.md", "r", encoding="utf-8") as f:
        user_prompt = f.read()

    user_prompt = user_prompt.replace(
        "<<ACTION_TRACE>>",
        action_trace
    )

    user_prompt = user_prompt.replace(
        "<<GOAL>>",
        goal
    )

    user_prompt = user_prompt.replace(
        "<<ELEMENTS>>",
        items_str
    )

    user_prompt = user_prompt.replace(
        "<<VIEWPORT_INFO>>",
        viewport_info
    )

    disallowed_act = None
    if state == 3:
        disallowed_act = tree.get_disallowed_actions(current_node, tree.nodes[current_node].state)
        disallowed_act_str = json.dumps(disallowed_act, ensure_ascii=False, indent=2)
        print(disallowed_act_str)
        user_prompt = user_prompt.replace(
            "<<DISALLOWED_ACTIONS>>",
            disallowed_act_str
        )
    else:
        user_prompt = user_prompt.replace(
            "<<DISALLOWED_ACTIONS>>",
            "NONE"
        )

    raw_resp, action_resp = vlm.generate(user_prompt=user_prompt, image_path=image_path, system_prompt=system_prompt)
    return raw_resp, action_resp, disallowed_act, system_prompt + '\n' + user_prompt


def extract_elements_from_elem_resps(elem_resps, sanitized_elements):
    selected_elements = []

    for resp_str in elem_resps:
        try:
            data = json.loads(resp_str)
        except json.JSONDecodeError:
            continue

        selected = data.get("selected")
        if not selected:
            continue

        idx = selected.get("index")
        if not isinstance(idx, int):
            continue

        elem_idx = idx - 1
        if 0 <= elem_idx < len(sanitized_elements):
            selected_elements.append(sanitized_elements[elem_idx])

    return selected_elements


def ret_all_hits(tree, driver, current_node):
    node = tree.nodes[current_node]

    bbox = node.action['bbox']
    print(node.action)
    print(node.action['action_type'])
    print(bbox)

    window_bbox = ret_quadrant_bbox(driver, bbox)
    print(window_bbox)

    time.sleep(2)

    hits = collect_overllaping_elements(driver, window_bbox, node.action['action_type'], pad=0, min_area=50, min_elem_area=600)

    print("inputs:", len(hits["inputs"]))
    print("clickables:", len(hits["clickables"]))

    all_hits = flatten_elements(hits, include_others=True)

    viewport_wh = get_viewport_wh(driver)
    filtered_elements = drop_giant_containers(all_hits, viewport_wh=viewport_wh)
    sanitized_elements, raw_htmls = sanitize_elements(filtered_elements, driver, key='el')

    return all_hits, filtered_elements, sanitized_elements, raw_htmls


def ret_all_hits_(driver, action):

    window_bbox = ret_quadrant_bbox(driver, action['bbox'])
    print(window_bbox)

    time.sleep(2)

    hits = collect_overllaping_elements(driver, window_bbox, action['action_type'], pad=0, min_area=50, min_elem_area=600)

    print("inputs:", len(hits["inputs"]))
    print("clickables:", len(hits["clickables"]))

    all_hits = flatten_elements(hits, include_others=True)

    viewport_wh = get_viewport_wh(driver)
    filtered_elements = drop_giant_containers(all_hits, viewport_wh=viewport_wh)
    sanitized_elements, raw_htmls = sanitize_elements(filtered_elements, driver, key='el')

    return all_hits, filtered_elements, sanitized_elements, raw_htmls


def element_selector(vlm, tree, driver, bbox_dir, current_node, CHUNK_SIZE=50):
    node = tree.nodes[current_node]

    bbox = node.action['bbox']

    print(bbox)

    window_bbox = ret_quadrant_bbox(driver, bbox)

    time.sleep(2)

    hits = collect_overllaping_elements(driver, window_bbox, node.action['action_type'], pad=0, min_area=50, min_elem_area=300)

    print("inputs:", len(hits["inputs"]))
    print("clickables:", len(hits["clickables"]))

    all_hits = flatten_elements(hits, include_others=True)

    viewport_wh = get_viewport_wh(driver)
    filtered_elements = drop_giant_containers(all_hits, viewport_wh=viewport_wh)
    sanitized_elements, raw_htmls = sanitize_elements(filtered_elements, driver, key='el')

    print(len(all_hits))
    print(len(filtered_elements))

    if len(sanitized_elements) != len(filtered_elements):
        raise RuntimeError(
            f"Sanitization failed:\nsanitized_elements: {len(sanitized_elements)}\nfiltered_elements: {len(filtered_elements)}"
        )

    save_window_bbox_png(
        driver,
        window_bbox,
        out_path=f"{bbox_dir}/{current_node}.png"
    )

    with open("./prompt/3_ElementSelection_system.md", "r", encoding="utf-8") as f:
        system_prompt = f.read()

    with open("./prompt/3_ElementSelection_user.md", "r", encoding="utf-8") as f:
        user_prompt_template = f.read()

    action_copy = node.action.copy()
    action_copy.pop("bbox", None)

    action_str = json.dumps(action_copy, indent=2, ensure_ascii=False)

    elem_resps = []
    raw_resps = []

    if len(sanitized_elements) == 0:
        elements_str = "NONE"

        user_prompt = user_prompt_template.replace("<<ACTION>>", action_str)
        user_prompt = user_prompt.replace("<<ELEMENTS>>", elements_str)

        raw_resp, elem_resp = vlm.generate(user_prompt=user_prompt, system_prompt=system_prompt)
        print("elem_resp:\n", elem_resp)
        elem_resps.append(elem_resp)
        raw_resps.append(raw_resp)

    for chunk_idx in range(0, len(sanitized_elements), CHUNK_SIZE):
        chunk = sanitized_elements[chunk_idx:chunk_idx + CHUNK_SIZE]
        print(f"[Chunk {chunk_idx // CHUNK_SIZE + 1}]")

        elements_str = "\n".join(chunk)

        user_prompt = user_prompt_template.replace("<<ACTION>>", action_str)
        user_prompt = user_prompt.replace("<<ELEMENTS>>", elements_str)

        raw_resp, elem_resp = vlm.generate(user_prompt=user_prompt, system_prompt=system_prompt)
        print("elem_resp:\n", elem_resp)
        elem_resps.append(elem_resp)
        raw_resps.append(raw_resp)

    if len(elem_resps) == 1:
        final_elem_resp = elem_resps[0]
    elif len(elem_resps) >= 2:
        print(f"[FINAL Chunk]")
        refined_elements = extract_elements_from_elem_resps(
            elem_resps,
            sanitized_elements
        )

        if not refined_elements:
            final_elem_resp = None
            print("no refined elements")
        else:
            elements_str = "\n".join(refined_elements)

            user_prompt = user_prompt_template.replace("<<ACTION>>", action_str)
            user_prompt = user_prompt.replace("<<ELEMENTS>>", elements_str)

            raw_resp, final_elem_resp = vlm.generate(user_prompt=user_prompt, system_prompt=system_prompt)
            elem_resps.append(final_elem_resp)
            raw_resps.append(raw_resp)

    return raw_resps, final_elem_resp, elem_resps, all_hits, filtered_elements, sanitized_elements, raw_htmls, window_bbox, system_prompt + '\n' + user_prompt


def call_vlm(vlm, system_prompt, user_prompt, image_path):

    raw_resp, resp = vlm.generate(user_prompt=user_prompt, image_path=image_path, system_prompt=system_prompt)

    return raw_resp, resp
