"""Structural validation of the shipped n8n workflow definition.

``n8n/techintel-pipeline.json`` is hand-written, so it can break silently: a
renamed node leaves a dangling connection, a mistyped node type makes n8n reject
the import outright, and a missing parameter turns a node into a no-op. These
checks catch all three without needing n8n installed.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / "n8n" / "techintel-pipeline.json"

#: ``node type -> (minimum typeVersion, required parameter names)``.
#: Types and parameter names were taken from the n8n node sources.
KNOWN_NODE_TYPES: Dict[str, Any] = {
    "n8n-nodes-base.manualTrigger": (1, ()),
    "n8n-nodes-base.scheduleTrigger": (1, ("rule",)),
    "n8n-nodes-base.executeCommand": (1, ("command",)),
    "n8n-nodes-base.if": (2, ("conditions",)),
    "n8n-nodes-base.readWriteFile": (1, ("operation", "fileSelector")),
    "n8n-nodes-base.noOp": (1, ()),
    "n8n-nodes-base.stopAndError": (1, ("errorType", "errorMessage")),
}

#: Nodes that end a branch, so they must have no outgoing connections.
TERMINAL_TYPES = frozenset({"n8n-nodes-base.stopAndError"})


class N8nWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(WORKFLOW_PATH.is_file(), f"missing {WORKFLOW_PATH}")
        self.workflow: Dict[str, Any] = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
        self.nodes: List[Dict[str, Any]] = self.workflow["nodes"]
        self.by_name = {node["name"]: node for node in self.nodes}

    def test_top_level_shape(self):
        for key in ("name", "nodes", "connections"):
            with self.subTest(key=key):
                self.assertIn(key, self.workflow)

    def test_node_names_and_ids_are_unique(self):
        names = [node["name"] for node in self.nodes]
        ids = [node["id"] for node in self.nodes]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_node_has_the_required_fields(self):
        for node in self.nodes:
            with self.subTest(node=node.get("name")):
                for key in ("parameters", "id", "name", "type", "typeVersion", "position"):
                    self.assertIn(key, node)
                self.assertEqual(len(node["position"]), 2)

    def test_node_types_are_known_and_versions_supported(self):
        for node in self.nodes:
            with self.subTest(node=node["name"]):
                self.assertIn(node["type"], KNOWN_NODE_TYPES)
                minimum_version, _ = KNOWN_NODE_TYPES[node["type"]]
                self.assertGreaterEqual(node["typeVersion"], minimum_version)

    def test_required_parameters_are_present_and_non_empty(self):
        for node in self.nodes:
            _, required = KNOWN_NODE_TYPES[node["type"]]
            for parameter in required:
                with self.subTest(node=node["name"], parameter=parameter):
                    self.assertIn(parameter, node["parameters"])
                    self.assertNotEqual(node["parameters"][parameter], "")

    def test_every_connection_points_at_an_existing_node(self):
        for source, outputs in self.workflow["connections"].items():
            self.assertIn(source, self.by_name)
            for branch in outputs["main"]:
                for connection in branch:
                    with self.subTest(source=source, target=connection["node"]):
                        self.assertIn(connection["node"], self.by_name)
                        self.assertEqual(connection["type"], "main")

    def test_terminal_nodes_have_no_outgoing_connections(self):
        for node in self.nodes:
            if node["type"] in TERMINAL_TYPES:
                with self.subTest(node=node["name"]):
                    self.assertNotIn(node["name"], self.workflow["connections"])

    def test_both_triggers_reach_the_pipeline(self):
        for trigger in ("Manual Trigger", "Schedule Trigger"):
            with self.subTest(trigger=trigger):
                targets = [
                    c["node"] for c in self.workflow["connections"][trigger]["main"][0]
                ]
                self.assertEqual(targets, ["Run pipeline"])

    def test_if_nodes_wire_both_branches(self):
        for node in self.nodes:
            if node["type"] != "n8n-nodes-base.if":
                continue
            with self.subTest(node=node["name"]):
                branches = self.workflow["connections"][node["name"]]["main"]
                self.assertEqual(len(branches), 2)
                self.assertTrue(branches[0], "true branch is unwired")
                self.assertTrue(branches[1], "false branch is unwired")

    def test_if_conditions_use_the_verified_filter_shape(self):
        for node in self.nodes:
            if node["type"] != "n8n-nodes-base.if":
                continue
            conditions = node["parameters"]["conditions"]
            with self.subTest(node=node["name"]):
                self.assertEqual(conditions["combinator"], "and")
                self.assertIn("version", conditions["options"])
                self.assertTrue(conditions["conditions"])
                for condition in conditions["conditions"]:
                    self.assertIn("leftValue", condition)
                    self.assertIn("rightValue", condition)
                    self.assertIn("type", condition["operator"])
                    self.assertIn("operation", condition["operator"])

    def test_execute_command_nodes_keep_going_on_failure(self):
        # Without onError=continueRegularOutput the exit code never reaches the
        # IF node, because ExecuteCommand throws on a non-zero exit.
        commands = [n for n in self.nodes if n["type"] == "n8n-nodes-base.executeCommand"]
        self.assertTrue(commands)
        for node in commands:
            with self.subTest(node=node["name"]):
                self.assertEqual(node.get("onError"), "continueRegularOutput")

    def test_pipeline_and_verify_are_both_invoked(self):
        commands = " ".join(
            node["parameters"]["command"]
            for node in self.nodes
            if node["type"] == "n8n-nodes-base.executeCommand"
        )
        self.assertIn("techintel.pipeline", commands)
        self.assertIn("techintel.verify", commands)

    def test_commands_contain_no_hardcoded_absolute_paths(self):
        # Same rule the notebooks follow: the workflow must work on any machine.
        for node in self.nodes:
            if node["type"] != "n8n-nodes-base.executeCommand":
                continue
            command = node["parameters"]["command"]
            with self.subTest(node=node["name"]):
                self.assertNotIn(":\\", command)
                self.assertNotIn("/Users/", command)
                self.assertNotIn("/home/", command)

    def test_file_read_points_inside_the_output_directory(self):
        readers = [n for n in self.nodes if n["type"] == "n8n-nodes-base.readWriteFile"]
        self.assertTrue(readers)
        for node in readers:
            with self.subTest(node=node["name"]):
                self.assertEqual(node["parameters"]["operation"], "read")
                self.assertTrue(node["parameters"]["fileSelector"].startswith("out/"))

    def test_schedule_trigger_uses_a_supported_interval_field(self):
        schedulers = [n for n in self.nodes if n["type"] == "n8n-nodes-base.scheduleTrigger"]
        self.assertTrue(schedulers)
        supported = {"seconds", "minutes", "hours", "days", "weeks", "months", "cronExpression"}
        for node in schedulers:
            for interval in node["parameters"]["rule"]["interval"]:
                with self.subTest(node=node["name"], field=interval["field"]):
                    self.assertIn(interval["field"], supported)

    def test_every_node_is_reachable_from_a_trigger(self):
        adjacency = {
            source: [connection["node"] for branch in outputs["main"] for connection in branch]
            for source, outputs in self.workflow["connections"].items()
        }
        triggers = [n["name"] for n in self.nodes if n["type"].endswith("Trigger")]
        self.assertTrue(triggers)

        seen, queue = set(triggers), list(triggers)
        while queue:
            for target in adjacency.get(queue.pop(), []):
                if target not in seen:
                    seen.add(target)
                    queue.append(target)

        self.assertEqual(sorted(set(self.by_name) - seen), [])
