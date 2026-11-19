"""The team's standard Trend support tools. Links are not stored here: they are added on the VM with
`python -m app.cli seed-tools --url <OneDrive folder or file link>` or in the Tools page."""

TOOLS = [
    {"key": "dsa-cut", "name": "Deep Security Agent Cleanup Tool (DSA CUT)", "product": "deep-security",
     "file_name": "DSA_CUT(2026Q4)_Build_1.0.1.67.zip",
     "description": "Force-removes the Deep Security Agent when the normal uninstall fails. Follow the force uninstall "
                    "guide first, run the tool as administrator, then reboot."},
    {"key": "dsa-support-tool", "name": "Deep Security Agent Support Tool (GUI)", "product": "deep-security",
     "file_name": "DSASupportTool_GUI(26Q3)-Build_1.0…zip",
     "description": "Trend's support tool for the Deep Security Agent with a GUI: collects agent diagnostics for a Trend "
                    "case and helps with common agent problems."},
    {"key": "scut", "name": "Apex One Security Agent Cleanup Tool (SCUT)", "product": "apex-one",
     "file_name": "SCUT_November2026.zip",
     "description": "Removes the Apex One / Standard Endpoint Protection agent when the normal uninstall fails. "
                    "Run as administrator and reboot."},
    {"key": "cdt", "name": "Case Diagnostic Tool (CDT)", "product": "apex-one",
     "file_name": "cdt-26-win-en-1270.zip",
     "description": "Collects debug logs while you reproduce a problem (Apex One agent or server and other Trend Windows "
                    "products). Attach the zip it creates to the Trend case."},
    {"key": "easyfix-cert", "name": "EasyFix for System Certificate", "product": "other",
     "file_name": "EasyFix_for_System_Certificate_V1.0.0.1…zip",
     "description": "Trend EasyFix for Windows system certificate problems, for example when an agent can't connect or "
                    "update because a required root certificate is missing."},
]
