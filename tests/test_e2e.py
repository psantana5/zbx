"""End-to-end integration tests for zbx CLI.

These tests run against a real Zabbix instance (configured via .env).
Each test creates and cleans up its own templates with unique names.

Test coverage:
  - zbx validate (valid + invalid YAML)
  - zbx plan     (new template, no-op, modification)
  - zbx apply    (create, update, idempotency)
  - zbx diff     (in sync, out of sync)
  - zbx export   (basic, full feature round-trip)
  - Full restore  (export → delete → apply → idempotent)
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests.conftest import minimal_template, rich_template, zabbix_required

pytestmark = zabbix_required  # skip all tests if Zabbix is not reachable

REPO_ROOT = Path(__file__).parent.parent
ZBX = [sys.executable, "-m", "zbx.cli"] if False else ["zbx"]


def run(*args: str, cwd: Path = REPO_ROOT, check: bool = True) -> subprocess.CompletedProcess:
    """Run zbx CLI and return the CompletedProcess."""
    result = subprocess.run(
        [*ZBX, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"zbx {' '.join(args)} exited {result.returncode}\n"
            f"STDOUT:\n{result.stdout}\n"
            f"STDERR:\n{result.stderr}"
        )
    return result


# ===========================================================================
# 1. VALIDATE
# ===========================================================================
class TestValidate:
    def test_valid_configs_pass(self):
        """zbx validate on the repo's configs/ should succeed."""
        r = run("validate", "configs/")
        assert r.returncode == 0
        assert "no schema errors" in r.stdout.lower() or "validated" in r.stdout.lower()

    def test_invalid_yaml_fails(self, tmp_yaml):
        """A YAML file with missing required 'template' field should fail validation."""
        bad = tmp_yaml({"items": [{"key": "agent.ping"}]}, "bad.yaml")
        r = run("validate", str(bad), check=False)
        assert r.returncode != 0

    def test_empty_template_name_fails(self, tmp_yaml):
        """template: '' should fail pydantic validation."""
        bad = tmp_yaml({"template": "", "items": []}, "bad2.yaml")
        r = run("validate", str(bad), check=False)
        assert r.returncode != 0

    def test_bad_interval_fails(self, tmp_yaml):
        """Invalid interval value should fail schema validation."""
        tmpl = minimal_template("zbx-validate-test")
        tmpl["items"][0]["interval"] = "badvalue"
        bad = tmp_yaml(tmpl, "bad_interval.yaml")
        r = run("validate", str(bad), check=False)
        assert r.returncode != 0

    def test_valid_rich_template_passes(self, tmp_yaml, unique_name):
        """Full-featured template YAML should pass validation."""
        p = tmp_yaml(rich_template(unique_name))
        r = run("validate", str(p))
        assert r.returncode == 0


# ===========================================================================
# 2. PLAN
# ===========================================================================
class TestPlan:
    def test_plan_new_template_shows_additions(
        self, tmp_yaml, unique_name, template_cleanup
    ):
        """plan on a template that doesn't exist should show all resources as + (add)."""
        template_cleanup(unique_name)
        p = tmp_yaml(minimal_template(unique_name))
        r = run("plan", str(p))
        assert r.returncode == 0
        assert "+ template:" in r.stdout
        assert "to add" in r.stdout

    def test_plan_after_apply_shows_no_changes(
        self, tmp_yaml, unique_name, template_cleanup
    ):
        """After apply, plan must show no changes (idempotency)."""
        template_cleanup(unique_name)
        p = tmp_yaml(minimal_template(unique_name))
        run("apply", str(p), "--auto-approve")
        r = run("plan", str(p))
        assert r.returncode == 0
        assert "no changes" in r.stdout.lower()

    def test_plan_modification_shows_modify(
        self, tmp_yaml, unique_name, template_cleanup
    ):
        """Changing an item's interval should appear as ~ (modify) in plan."""
        template_cleanup(unique_name)
        tmpl = minimal_template(unique_name)
        p = tmp_yaml(tmpl)
        run("apply", str(p), "--auto-approve")

        # Change interval and re-plan
        tmpl["items"][0]["interval"] = "120s"
        p2 = tmp_yaml(tmpl, "modified.yaml")
        r = run("plan", str(p2))
        assert r.returncode == 0
        assert "to modify" in r.stdout or "~" in r.stdout

    def test_plan_rich_template_shows_all_resources(
        self, tmp_yaml, unique_name, template_cleanup
    ):
        """Plan output for a rich template should list items, triggers, discovery rules."""
        template_cleanup(unique_name)
        p = tmp_yaml(rich_template(unique_name))
        r = run("plan", str(p))
        assert "item:" in r.stdout
        assert "trigger:" in r.stdout
        assert "discovery_rule:" in r.stdout


# ===========================================================================
# 3. APPLY
# ===========================================================================
class TestApply:
    def test_apply_creates_template(
        self, tmp_yaml, unique_name, template_cleanup, client
    ):
        """apply should create a new template in Zabbix."""
        template_cleanup(unique_name)
        p = tmp_yaml(minimal_template(unique_name))
        r = run("apply", str(p), "--auto-approve")
        assert r.returncode == 0
        assert "apply complete" in r.stdout.lower()
        matches = client.find_templates(unique_name)
        assert len(matches) == 1

    def test_apply_is_idempotent(
        self, tmp_yaml, unique_name, template_cleanup
    ):
        """Running apply twice on the same YAML should not error on the second run."""
        template_cleanup(unique_name)
        p = tmp_yaml(minimal_template(unique_name))
        run("apply", str(p), "--auto-approve")
        r = run("apply", str(p), "--auto-approve")
        assert r.returncode == 0

    def test_apply_updates_item_interval(
        self, tmp_yaml, unique_name, template_cleanup, client
    ):
        """Applying a modified YAML should update the item in Zabbix."""
        template_cleanup(unique_name)
        tmpl = minimal_template(unique_name)
        p = tmp_yaml(tmpl)
        run("apply", str(p), "--auto-approve")

        tmpl["items"][0]["interval"] = "120s"
        p2 = tmp_yaml(tmpl, "modified.yaml")
        run("apply", str(p2), "--auto-approve")

        matches = client.find_templates(unique_name)
        tid = matches[0]["templateid"]
        items = client._call("item.get", {
            "templateids": [tid],
            "output": ["key_", "delay"],
            "filter": {"key_": "agent.ping"},
        })
        assert items[0]["delay"] == "120s"

    def test_apply_rich_template(
        self, tmp_yaml, unique_name, template_cleanup, client
    ):
        """Apply should create all item types including dependent, calculated, and LLD."""
        template_cleanup(unique_name)
        p = tmp_yaml(rich_template(unique_name))
        r = run("apply", str(p), "--auto-approve")
        assert r.returncode == 0

        matches = client.find_templates(unique_name)
        tid = matches[0]["templateid"]

        items = client._call("item.get", {"templateids": [tid], "output": ["key_", "type"]})
        item_types = {i["key_"]: i["type"] for i in items}

        # Regular agent item
        assert "system.cpu.util[percpu,idle]" in item_types
        assert item_types["system.cpu.util[percpu,idle]"] == "0"
        # Dependent item
        assert item_types.get("system.cpu.util") == "18"
        # Calculated item
        assert item_types.get("system.cpu.load.percpu") == "15"

        rules = client._call("discoveryrule.get", {
            "templateids": [tid],
            "output": ["key_", "type"],
        })
        rule_types = {r["key_"]: r["type"] for r in rules}
        assert "net.if.discovery" in rule_types
        assert rule_types.get("vfs.fs.dependent.discovery") == "18"

    def test_apply_dry_run_flag(self, tmp_yaml, unique_name, template_cleanup, client):
        """apply --dry-run should not create anything in Zabbix."""
        template_cleanup(unique_name)
        p = tmp_yaml(minimal_template(unique_name))
        r = run("apply", str(p), "--dry-run", check=False)
        # dry-run should succeed (exit 0) and NOT create the template
        # (plan-only behavior when --dry-run is passed)
        assert r.returncode == 0 or "dry" in r.stdout.lower() or "plan" in r.stdout.lower()
        # Verify nothing was actually created
        matches = client.find_templates(unique_name)
        assert len(matches) == 0


# ===========================================================================
# 4. DIFF
# ===========================================================================
class TestDiff:
    def test_diff_after_apply_shows_no_diff(
        self, tmp_yaml, unique_name, template_cleanup
    ):
        """diff after apply should show no differences."""
        template_cleanup(unique_name)
        p = tmp_yaml(minimal_template(unique_name))
        run("apply", str(p), "--auto-approve")
        r = run("diff", str(p))
        assert r.returncode == 0
        assert "no changes" in r.stdout.lower() or "up-to-date" in r.stdout.lower()

    def test_diff_before_apply_shows_additions(
        self, tmp_yaml, unique_name, template_cleanup
    ):
        """diff on a non-existent template should show all resources as additions."""
        template_cleanup(unique_name)
        p = tmp_yaml(minimal_template(unique_name))
        r = run("diff", str(p))
        assert r.returncode == 0
        assert "+" in r.stdout

    def test_diff_detects_interval_change(
        self, tmp_yaml, unique_name, template_cleanup
    ):
        """diff should show a change when YAML interval differs from Zabbix."""
        template_cleanup(unique_name)
        tmpl = minimal_template(unique_name)
        p = tmp_yaml(tmpl)
        run("apply", str(p), "--auto-approve")

        tmpl["items"][0]["interval"] = "300s"
        p2 = tmp_yaml(tmpl, "modified.yaml")
        r = run("diff", str(p2))
        assert r.returncode == 0
        assert "~" in r.stdout or "modify" in r.stdout.lower() or "300s" in r.stdout


# ===========================================================================
# 5. EXPORT
# ===========================================================================
class TestExport:
    def test_export_produces_valid_yaml(
        self, tmp_yaml, unique_name, template_cleanup, tmp_path
    ):
        """export should produce a loadable YAML file."""
        template_cleanup(unique_name)
        p = tmp_yaml(minimal_template(unique_name))
        run("apply", str(p), "--auto-approve")

        out_file = tmp_path / "exported.yaml"
        r = run("export", unique_name)
        assert r.returncode == 0
        # Write stdout to file and parse it
        out_file.write_text(r.stdout)
        data = yaml.safe_load(r.stdout)
        assert data["template"] == unique_name
        assert any(i["key"] == "agent.ping" for i in data.get("items", []))

    def test_export_unknown_template_fails(self):
        """export on a non-existent template should fail gracefully."""
        r = run("export", "this-template-does-not-exist-xyz", check=False)
        assert r.returncode != 0

    def test_export_rich_template_preserves_fields(
        self, tmp_yaml, unique_name, template_cleanup
    ):
        """Exported YAML should contain master_item_key, params, filter, prototypes."""
        template_cleanup(unique_name)
        p = tmp_yaml(rich_template(unique_name))
        run("apply", str(p), "--auto-approve")

        r = run("export", unique_name)
        assert r.returncode == 0
        data = yaml.safe_load(r.stdout)

        items_by_key = {i["key"]: i for i in data.get("items", [])}
        # Dependent item has master_item_key
        assert "master_item_key" in items_by_key.get("system.cpu.util", {})
        # Calculated item has params
        assert "params" in items_by_key.get("system.cpu.load.percpu", {})

        rules_by_key = {r["key"]: r for r in data.get("discovery_rules", [])}
        assert "vfs.fs.dependent.discovery" in rules_by_key, (
            f"Dependent LLD rule missing from export. "
            f"Got rule keys: {list(rules_by_key.keys())}. "
            f"Full YAML stdout:\n{r.stdout[:3000]}"
        )
        dep_rule = rules_by_key.get("vfs.fs.dependent.discovery", {})
        assert dep_rule.get("type") == "dependent", f"dep_rule contents: {dep_rule}"
        assert dep_rule.get("master_item_key") == "vfs.fs.get", f"dep_rule contents: {dep_rule}"
        assert "filter" in dep_rule

        net_rule = rules_by_key.get("net.if.discovery", {})
        protos_by_key = {p["key"]: p for p in net_rule.get("item_prototypes", [])}
        # Dependent prototype has master_item_key
        assert "master_item_key" in protos_by_key.get("net.if.out[{#IFNAME}]", {})
        # Calculated prototype has params
        assert "params" in protos_by_key.get("net.if.total[{#IFNAME}]", {})


# ===========================================================================
# 6. FULL RESTORE TEST
# ===========================================================================
class TestFullRestore:
    def test_restore_minimal_template(
        self, tmp_yaml, unique_name, template_cleanup, client
    ):
        """export → delete → apply → plan(no changes) for a minimal template."""
        template_cleanup(unique_name)
        p = tmp_yaml(minimal_template(unique_name))
        run("apply", str(p), "--auto-approve")

        # Export
        r = run("export", unique_name)
        exported_yaml = r.stdout
        exported_data = yaml.safe_load(exported_yaml)

        # Delete
        matches = client.find_templates(unique_name)
        tid = matches[0]["templateid"]
        client._call("template.delete", [tid])

        # Restore from exported YAML
        export_file = Path(tmp_yaml.__self__._request.fspath.dirpath()) if hasattr(tmp_yaml, '__self__') else Path("/tmp") / f"{unique_name}.yaml"
        # Write exported YAML to temp file
        import tempfile, os
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(exported_yaml)
            export_path = f.name

        try:
            run("apply", export_path, "--auto-approve")
            # Idempotency check
            r2 = run("plan", export_path)
            assert "no changes" in r2.stdout.lower()
            # Verify in Zabbix
            matches = client.find_templates(unique_name)
            assert len(matches) == 1
            restored_tid = matches[0]["templateid"]
            items = client._call("item.get", {"templateids": [restored_tid], "output": ["key_"]})
            assert any(i["key_"] == "agent.ping" for i in items)
        finally:
            os.unlink(export_path)

    def test_restore_rich_template(
        self, tmp_yaml, unique_name, template_cleanup, client
    ):
        """Full restore test: export → delete → apply for rich template with all types.

        Verifies that ALL of the following are restored:
          - regular items
          - dependent items (master_item_key)
          - calculated items (params formula)
          - trigger with tags
          - non-dependent LLD rule with filter + prototypes (regular/dependent/calculated)
          - dependent LLD rule (master_item_key) with filter + prototypes
        """
        template_cleanup(unique_name)
        p = tmp_yaml(rich_template(unique_name))
        run("apply", str(p), "--auto-approve")

        # Step 1: Export
        r = run("export", unique_name)
        assert r.returncode == 0
        exported_yaml = r.stdout
        exported_data = yaml.safe_load(exported_yaml)

        # Verify export completeness
        items_by_key = {i["key"]: i for i in exported_data.get("items", [])}
        assert "master_item_key" in items_by_key.get("system.cpu.util", {}), "dependent item missing master_item_key"
        assert "params" in items_by_key.get("system.cpu.load.percpu", {}), "calculated item missing params"

        rules_by_key = {r["key"]: r for r in exported_data.get("discovery_rules", [])}
        assert "vfs.fs.dependent.discovery" in rules_by_key, (
            f"Dependent LLD rule missing from exported YAML. "
            f"Got rule keys: {list(rules_by_key.keys())}. "
            f"Full YAML:\n{exported_yaml[:3000]}"
        )
        dep_rule = rules_by_key.get("vfs.fs.dependent.discovery", {})
        assert dep_rule.get("master_item_key") == "vfs.fs.get", f"dep_rule: {dep_rule}"

        # Step 2: Delete
        matches = client.find_templates(unique_name)
        client._call("template.delete", [matches[0]["templateid"]])

        # Step 3: Apply exported YAML
        import tempfile, os
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(exported_yaml)
            export_path = f.name

        try:
            run("apply", export_path, "--auto-approve")

            # Step 4: Verify idempotency
            r2 = run("plan", export_path)
            assert "no changes" in r2.stdout.lower(), f"Expected no changes, got:\n{r2.stdout}"

            # Step 5: Verify restored objects in Zabbix
            matches = client.find_templates(unique_name)
            assert len(matches) == 1
            tid = matches[0]["templateid"]

            items = client._call("item.get", {
                "templateids": [tid],
                "output": ["key_", "type", "params", "master_itemid"],
            })
            item_map = {i["key_"]: i for i in items}

            # Regular item
            assert "system.cpu.util[percpu,idle]" in item_map
            assert item_map["system.cpu.util[percpu,idle]"]["type"] == "0"
            # Dependent item
            dep = item_map.get("system.cpu.util", {})
            assert dep.get("type") == "18", "CPU util should be dependent"
            assert dep.get("master_itemid") != "0", "Dependent item must have master_itemid"
            # Calculated item with formula
            calc = item_map.get("system.cpu.load.percpu", {})
            assert calc.get("type") == "15", "CPU load should be calculated"
            assert calc.get("params"), "Calculated item must have params"

            # Triggers
            triggers = client._call("trigger.get", {
                "templateids": [tid],
                "output": ["description"],
                "inherited": False,
            })
            assert len(triggers) == 1

            # Discovery rules
            rules = client._call("discoveryrule.get", {
                "templateids": [tid],
                "output": ["key_", "type", "master_itemid"],
            })
            rule_map = {r["key_"]: r for r in rules}
            assert "net.if.discovery" in rule_map
            dep_rule = rule_map.get("vfs.fs.dependent.discovery", {})
            assert dep_rule.get("type") == "18", "FS discovery should be dependent"
            assert dep_rule.get("master_itemid") != "0", "Dependent rule must have master_itemid"

            # Item prototypes on network rule
            net_rule_id = rule_map["net.if.discovery"]["itemid"]
            net_protos = client._call("itemprototype.get", {
                "discoveryids": [net_rule_id],
                "output": ["key_", "type", "params", "master_itemid"],
            })
            net_proto_map = {p["key_"]: p for p in net_protos}
            assert "net.if.in[{#IFNAME}]" in net_proto_map
            dep_proto = net_proto_map.get("net.if.out[{#IFNAME}]", {})
            assert dep_proto.get("type") == "18", "out proto should be dependent"
            assert dep_proto.get("master_itemid") != "0"
            calc_proto = net_proto_map.get("net.if.total[{#IFNAME}]", {})
            assert calc_proto.get("type") == "15", "total proto should be calculated"
            assert calc_proto.get("params"), "Calculated proto must have params"

            # Item prototypes on filesystem rule
            fs_rule_id = dep_rule["itemid"]
            fs_protos = client._call("itemprototype.get", {
                "discoveryids": [fs_rule_id],
                "output": ["key_"],
            })
            fs_proto_keys = {p["key_"] for p in fs_protos}
            assert "vfs.fs.size[{#FSNAME},used]" in fs_proto_keys
            assert "vfs.fs.size[{#FSNAME},free]" in fs_proto_keys

        finally:
            os.unlink(export_path)
