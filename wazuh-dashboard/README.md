# Wazuh SOC Triage dashboard

Importable OpenSearch Dashboards saved objects (Dashboards Management > Saved objects > Import).
Regenerate with `python3 build_dashboard.py`.

Assumes the Wazuh-managed index pattern with id `wazuh-alerts-*` exists and uses `timestamp` as its time field.
Verify under Dashboards Management > Index patterns before importing.

24h panels carry their own `timestamp >= "now-24h"` filter; leave the dashboard range at 7d.
