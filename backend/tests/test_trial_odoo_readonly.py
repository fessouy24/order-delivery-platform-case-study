import pytest

from apps.operations.providers import IntegrationError, Odoo


@pytest.mark.parametrize("method", ["create", "write", "unlink", "action_confirm"])
def test_trial_connection_blocks_remote_mutations_before_any_network_call(monkeypatch, method):
    monkeypatch.setenv("ODOO_TRIAL_READ_ONLY", "true")
    client = object.__new__(Odoo)
    with pytest.raises(IntegrationError, match="read-only"):
        client.call("sale.order", method)
