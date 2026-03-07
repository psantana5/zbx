from unittest.mock import patch, MagicMock
import pytest
from typer.testing import CliRunner
from zbx.commands import host, hostgroup, macro, plan, apply

runner = CliRunner()

@patch("zbx.commands.host.ZabbixClient")
def test_host_list(mock_zabbix_client):
    mock_client = MagicMock()
    mock_zabbix_client.return_value.__enter__.return_value = mock_client

    mock_client.list_hosts.return_value = [
        {
            "host": "TestHost",
            "name": "Test Host",
            "interfaces": [{"ip": "127.0.0.1", "port": "10050", "main": "1"}],
            "groups": [{"name": "TestGroup"}],
            "status": "0",
            "parentTemplates": [{"host": "Template OS Linux"}]
        }
    ]

    result = runner.invoke(host.app, ["list"])

    mock_client.list_hosts.assert_called_once()
    assert result.exit_code == 0
    assert "TestHost" in result.output
    assert "127.0.0.1:10050" in result.output

@patch("zbx.commands.hostgroup.ZabbixClient")
def test_hostgroup_list(mock_zabbix_client):
    mock_client = MagicMock()
    mock_zabbix_client.return_value.__enter__.return_value = mock_client

    mock_client.list_hostgroups.return_value = [
        {"groupid": "1", "name": "Linux Servers"},
        {"groupid": "2", "name": "Windows Servers"}
    ]

    result = runner.invoke(hostgroup.app, ["list"])

    mock_client.list_hostgroups.assert_called_once()
    assert result.exit_code == 0
    assert "Linux Servers" in result.output
    assert "Windows Servers" in result.output

@patch("zbx.commands.macro.ZabbixClient")
def test_macro_list(mock_zabbix_client):
    mock_client = MagicMock()
    mock_zabbix_client.return_value.__enter__.return_value = mock_client

    mock_client.list_global_macros.return_value = [
        {"macro": "{$MACRO1}", "value": "value1", "description": "Description 1"},
        {"macro": "{$MACRO2}", "value": "value2", "description": "Description 2"}
    ]

    result = runner.invoke(macro.app, ["list"])

    mock_client.list_global_macros.assert_called_once()
    assert result.exit_code == 0
    assert "{$MACRO1}" in result.output
    assert "value1" in result.output
    assert "Description 1" in result.output

@patch("zbx.commands.plan.ZabbixClient")
@patch("zbx.commands.plan.save_plan")
def test_plan_dry_run_and_output(mock_save_plan, mock_zabbix_client):
    mock_client = MagicMock()
    mock_zabbix_client.return_value.__enter__.return_value = mock_client

    mock_client.plan.return_value = {"changes": "template changes"}
    mock_client.plan_host.return_value = {"changes": "host changes"}

    result = runner.invoke(plan.app, ["/path/to/configs", "--dry-run", "--output", "plan.json"])

    mock_zabbix_client.assert_called_once()
    mock_save_plan.assert_called_once()
    assert result.exit_code == 0
    assert "Plan saved to" in result.output

@patch("zbx.commands.apply.ZabbixClient")
def test_apply_dry_run(mock_zabbix_client):
    mock_client = MagicMock()
    mock_zabbix_client.return_value.__enter__.return_value = mock_client

    mock_client.plan.return_value = {"changes": "template changes"}
    mock_client.plan_host.return_value = {"changes": "host changes"}

    result = runner.invoke(apply.app, ["/path/to/configs", "--dry-run"])

    mock_zabbix_client.assert_called_once()
    assert result.exit_code == 0
    assert "Dry-run mode" in result.output

@patch("zbx.commands.apply.SavedPlan.load")
@patch("zbx.commands.apply.ZabbixClient")
def test_apply_from_plan(mock_zabbix_client, mock_saved_plan_load):
    mock_client = MagicMock()
    mock_zabbix_client.return_value.__enter__.return_value = mock_client

    mock_saved_plan = MagicMock()
    mock_saved_plan_load.return_value = mock_saved_plan
    mock_saved_plan.configs_path = "/path/to/configs"
    mock_saved_plan.template_diffs = [{"changes": "template changes"}]
    mock_saved_plan.host_diffs = [{"changes": "host changes"}]

    result = runner.invoke(apply.app, ["--from-plan", "plan.json"])

    mock_saved_plan_load.assert_called_once_with("plan.json")
    mock_zabbix_client.assert_not_called()  # No ZabbixClient calls in --from-plan
    assert result.exit_code == 0
    assert "Loaded plan from" in result.output
    assert "Saved Plan" in result.output