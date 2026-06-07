from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

@dataclass
class ToTNode:
    id: int
    parent_id: Optional[int]
    href: str
    depth: int
    state: int

    path_actions: List[Dict[str, Any]] = field(default_factory=list)
    action: Dict[str, Any] = field(default_factory=dict)
    element: Dict[str, Any] = field(default_factory=dict)

    confidence_score: int = 0
    relevance_score: int = 0

    expanded: bool = False
    pruned: bool = False
    prune_reason: Optional[str] = None

    children: List[int] = field(default_factory=list)


import json
from pathlib import Path
from typing import Dict, List, Optional

class ToTTree:
    def __init__(self):
        self._node_seq = -1
        self.nodes: Dict[int, ToTNode] = {}

    def new_node_id(self) -> int:
        self._node_seq += 1
        return self._node_seq

    def add_root(
        self,
        node_id: int,
        href: str,
        state: int = 0,
        confidence_score: int = 0,
        relevance_score: int = 0,
        element: Dict[str, Any] = None,
    ) -> None:
        self.nodes[node_id] = ToTNode(
            id=node_id,
            parent_id=None,
            href=href,
            depth=0,
            state=state,
            path_actions=[],
            confidence_score=confidence_score,
            relevance_score=relevance_score,
            element=element or {},
        )

    def add_child(
        self,
        node_id: int,
        parent_id: int,
        action: Dict[str, Any],
        state: int,
        confidence_score: int,
        href: str = "",
        relevance_score: int = 0,
        element: Dict[str, Any] = None,
    ) -> None:
        parent = self.nodes[parent_id]

        child = ToTNode(
            id=node_id,
            parent_id=parent_id,
            href=href,
            depth=parent.depth + 1,
            state=state,
            path_actions=parent.path_actions + [action],
            action=action,
            confidence_score=confidence_score,
            relevance_score=relevance_score,
            element=element or {},
        )

        self.nodes[node_id] = child
        parent.children.append(node_id)

    def update_element(self, node_id: str, element: Dict[str, Any]) -> None:
        node = self.nodes[node_id]
        node.element = element

    def update_relevance(self, node_id: str, new_relevance: int) -> None:
        node = self.nodes[node_id]
        node.relevance_score = new_relevance

    def update_href(self, node_id: str, href: str) -> None:
        node = self.nodes[node_id]
        node.href = href

    def mark_expanded(self, node_id: str) -> None:
        node = self.nodes[node_id]
        if node.pruned:
            return
        node.expanded = True

    def prune_node(self, node_id: str, reason: str = "below_threshold") -> None:
        node = self.nodes[node_id]
        if node.pruned:
            return

        node.pruned = True
        node.prune_reason = reason

    def pick_next_to_expand(self, current_node_id: int) -> Optional[ToTNode]:
        cur = self.nodes[current_node_id]

        def pick_by_state(target_state: int) -> Optional[ToTNode]:
            best = None
            best_key = None

            for nid, node in self.nodes.items():
                if nid == current_node_id:
                    continue
                if node.pruned or node.expanded:
                    continue
                if node.state != target_state:
                    continue

                is_sibling = 1 if node.parent_id != cur.parent_id else 0

                ancestor_dist = self._ancestor_distance(current_node_id, nid)

                depth_diff = abs(node.depth - cur.depth)

                key = (
                )

                if best is None or key < best_key:
                    best = node
                    best_key = key

            return best

        return pick_by_state(cur.state) or pick_by_state(3)

    def _ancestor_distance(self, nid_a: int, nid_b: int) -> int:
        ancestors_a = set()
        cur = nid_a
        while cur is not None:
            ancestors_a.add(cur)
            cur = self.nodes[cur].parent_id

        cur = nid_b
        dist_b = 0
        while cur is not None:
            if cur in ancestors_a:
                dist_a = 0
                walk = nid_a
                while walk != cur:
                    dist_a += 1
                    walk = self.nodes[walk].parent_id
                return dist_a + dist_b
            dist_b += 1
            cur = self.nodes[cur].parent_id

    def get_disallowed_actions(
        self,
        current_node_id: int,
        target_state: int,
        depth_window: int = 10,
    ) -> str:
        cur = self.nodes[current_node_id]
        k = cur.depth

        lo = max(0, k - depth_window)
        hi = k + depth_window

        actions = []
        for node in self.nodes.values():
            if not (lo <= node.depth <= hi):
                continue
            if node.state != target_state:
                continue
            if not node.action:
                continue
            if node.action.get("action_type") == "scroll" or node.action.get("action_type") == "type" or node.action.get("action_type") == "wait":
                continue
            if node.expanded == False and node.pruned == False:
                continue

            if node.pruned and node.prune_reason == "low confidence":
                continue

            clean_action = {
                kk: vv for kk, vv in node.action.items()
                if kk not in ("rationale")
            }
            actions.append(clean_action)

        return actions

    def get_path_node_ids(self, node_id: str) -> List[str]:
        path = []
        cur = node_id
        while cur is not None:
            node = self.nodes[cur]
            path.append(node.id)
            cur = node.parent_id
        return list(reversed(path))

    from typing import Dict, List, Tuple

    def get_pwd_actions(
    self,
    node_id: int,
    ) -> Tuple[str, List[Dict[str, object]]]:

        if node_id not in self.nodes:
            raise KeyError(f"Node not found: {node_id}")

        node = self.nodes[node_id]
        target_state = node.state
        target_url = node.href

        if node.parent_id is None:
            return (target_url, [])

        path_ids = self.get_path_node_ids(node.id)
        path_nodes = [self.nodes[nid] for nid in path_ids]

        last_change_idx = 0
        for i in range(1, len(path_nodes)):
            if path_nodes[i].href != path_nodes[i - 1].href:
                last_change_idx = i

        actions: List[Dict[str, object]] = []

        for n in path_nodes[last_change_idx:]:
            if (n.state != target_state) and (n.state != 4) and (n.state != 6):
                continue
            if not n.element:
                continue
            actions.append(n.element)

        return (target_url, actions)

    def get_replay_actions(
        self,
        target_node_id: int,
    ) -> Tuple[str, List[Dict[str, object]]]:

        if target_node_id not in self.nodes:
            raise KeyError(f"Node not found: {target_node_id}")

        target = self.nodes[target_node_id]
        target_state = target.state

        if target.parent_id is None:
            return (target.href, [])

        base = self.nodes[target.parent_id]
        target_url = base.href

        path_ids = self.get_path_node_ids(base.id)
        print("path_ids: ", path_ids)
        path_nodes = [self.nodes[nid] for nid in path_ids]

        last_change_idx = 0
        for i in range(1, len(path_nodes)):
            if path_nodes[i].href != path_nodes[i - 1].href:
                last_change_idx = i
        print("last_change_idx: ", last_change_idx)

        actions: List[Dict[str, object]] = []

        base_idx = len(path_nodes) - 1
        prev_node = path_nodes[base_idx - 1] if base_idx - 1 >= 0 else None

        for n in path_nodes[last_change_idx:base_idx + 1]:

            if (n.state != target_state) and (n.state != 4) and (n.state != 6):
                continue
            if not n.element:
                continue
            actions.append(n.element)

        return (target_url, actions)

    def _action_equiv_key(self, node_id: int) -> Optional[tuple]:
        node = self.nodes[node_id]
        a = node.action
        if not a:
            return None

        if a.get("text_hint") == None and a.get("icon_hint") == "other":
            return None
        if a.get("action_type") in ["scroll", "CAPTCHA"]:
            return None

        return (
            node.href,
            a.get("action_type"),
            a.get("kind"),
            a.get("text_hint"),
            a.get("icon_hint"),
        )

    def find_equiv_node_ids(self, node_id: int) -> List[int]:
        key = self._action_equiv_key(node_id)
        if key is None:
            return []

        return [
            nid for nid in self.nodes
            if nid != node_id and self._action_equiv_key(nid) == key
        ]

    def save_tree(self, path: str) -> None:
        payload = {
            "node_seq": self._node_seq,
            "nodes": [vars(n) for n in self.nodes.values()],
        }
        Path(path).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load_tree(cls, path: str) -> "ToTTree":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))

        tree = cls()
        tree._node_seq = payload["node_seq"]

        for nd in payload["nodes"]:
            tree.nodes[nd["id"]] = ToTNode(**nd)

        return tree


import networkx as nx
import matplotlib.pyplot as plt
from textwrap import shorten

def _short(s, width=45):
    if not s:
        return ""
    return shorten(str(s), width=width, placeholder="…")

def _short_action(action):

    action_str = str()

    if not action:
        action_str = ""
        return action_str
    action_str += "[" + action.get("action_type") + "]"

    if action.get("text_hint") is not None:
        action_str += action["text_hint"]
    if action.get("icon_hint") is not None:
        action_str += action["icon_hint"]

    return action_str


import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import networkx as nx
import os

def visualize_tot_tree(tree, tree_path, rank, domain, today, model, figsize=(14, 8)):
    G = nx.DiGraph()

    frontier_ids = {
        nid for nid, n in tree.nodes.items()
        if (not n.expanded and not n.pruned)
    }

    for node_id, node in tree.nodes.items():
        if node.pruned:
            state = "PRUNED"
        elif node.expanded:
            state = "EXPANDED"
        else:
            state = "FRONTIER"

        label = (
            f"id={node.id}\n"
            f"depth={node.depth} | state={node.state}\n"
            f"conf={node.confidence_score} | rel={node.relevance_score}\n"
            f"{_short(node.href)}\n"
            f"act: {_short_action(node.action)}"
        )

        G.add_node(
            node_id,
            label=label,
            depth=node.depth,
            pruned=node.pruned,
            expanded=node.expanded,
            frontier=(node_id in frontier_ids),
        )

        if node.parent_id is not None:
            G.add_edge(node.parent_id, node_id)

    depths = {}
    for nid, n in tree.nodes.items():
        depths.setdefault(n.depth, []).append(nid)

    pos = {}
    for d, nodes_at_depth in depths.items():
        y = -d
        count = len(nodes_at_depth)
        for i, nid in enumerate(sorted(nodes_at_depth)):
            x = 0.0 if count == 1 else (-1.2 + 2.4 * i / (count - 1))
            pos[nid] = (x, y)

    node_colors = []
    node_sizes = []
    node_linewidths = []

    for nid in G.nodes():
        n = tree.nodes[nid]

        if n.pruned:
            node_colors.append("#BDBDBD")
        elif not n.expanded:
            node_colors.append("#F4A261")
        else:
            node_colors.append("#4C78A8")

        if nid in frontier_ids:
            node_sizes.append(5200)
            node_linewidths.append(3.5)
        else:
            node_sizes.append(4200)
            node_linewidths.append(2.0)

    plt.figure(figsize=figsize)

    nx.draw_networkx_edges(
        G, pos,
        arrows=True,
        arrowstyle="-|>",
        arrowsize=16,
        width=1.6,
        alpha=0.7,
    )

    nx.draw_networkx_nodes(
        G,
        pos,
        node_shape="s",
        node_color=node_colors,
        node_size=node_sizes,
        linewidths=node_linewidths,
        edgecolors="black",
        alpha=0.95,
    )

    labels = nx.get_node_attributes(G, "label")
    for nid, (x, y) in pos.items():
        plt.text(
            x, y,
            labels[nid],
            ha="center",
            va="center",
            fontsize=8,
            family="monospace",
        )

    plt.axis("off")
    plt.tight_layout()

    plt.savefig(
        f"{tree_path}/{rank}_{domain}_{today}_{model}.png",
        dpi=300,
    )
    print(f"{tree_path}/{rank}_{domain}_{today}_{model}.png")

    plt.show()
