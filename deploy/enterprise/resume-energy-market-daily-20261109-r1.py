"""Install one original-preserving public Skill and its credential-isolated collector.

Host: datax-01. New resources only. No Gateway rollout, scheduler or Work delivery.
Python 3.7 host entry; Python 3.10 in the existing ordinary sandbox image.
"""

import argparse
import base64
import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
import uuid
import zipfile

NAMESPACE = "deer-flow"
PREFIX = "energy-market-20261109-r1"
DEPLOYMENT = "energy-market-collector"
SECRET = PREFIX + "-provider"
CONFIG = PREFIX + "-code"
POLICY = PREFIX + "-ingress"
PVC = "deer-flow-skills-rwx-pvc"
IMAGE = "deerflow.local/deer-flow-aio-sandbox:20260921-enterprise-arm64-rc15-document-runtime1"
NODE = "ecs-zdz-pro-0015"
NODE_UID = "de6ee5e8-dbfb-4272-9ebf-ae82182db085"
# Operator's fresh 2026-11-09 23:16 snapshot: twelve Ready ordinary containers
# on node 0017 agree. This is the expected identity, not evidence of node 0015's
# cache. The owned Never-pull maintenance Pod must prove that cache separately.
EXPECTED_IMAGE_ID = (
    "sha256:fb36911f899cc43f1228ee4d0701130761b43b3e9792320bf4e0f16f41f0bec8"
)
KUBE = [
    "kubectl",
    "--kubeconfig=/etc/kubernetes/admin.conf",
    "--request-timeout=15s",
    "-n",
    NAMESPACE,
]
PACKAGE_SHA = "0736b9e8e06ef32be75d2e19b388f53910681ad265135977ebaf16bd74a49d4d"
CODE_SHA = "764fd9821d1e3a9a69483361ee4a297ad1d9d2514f92c9af39a020dec0d7e3b9"
COLLECTOR_CODE_B64 = "IiIiSW50ZXJuYWwgZml4ZWQtcHJvdmlkZXIgY29sbGVjdG9yLiBNb3VudCBjcmVkZW50aWFscyBvbmx5IGluIHRoaXMgZGVkaWNhdGVkIHByb2Nlc3MuCgpUaGUgc2hhcmVkIFNraWxsIHN0YXlzIGJ5dGUtaWRlbnRpY2FsLiBPbmx5IGRhdGVkIG1hcmtldCBjYWNoZSBmaWxlcyBhcmUgZXhwb3NlZC4KTm8gcHVibGljIGluZ3Jlc3MsIGFyYml0cmFyeSBVUkwsIHVzZXIgZG9jdW1lbnQsIG1vZGVsIG9yIFdvcmsgb3BlcmF0aW9uIGlzIHN1cHBvcnRlZC4KIiIiCgppbXBvcnQgZGF0ZXRpbWUgYXMgZHQKaW1wb3J0IGltcG9ydGxpYi51dGlsCmltcG9ydCBqc29uCmltcG9ydCBvcwpmcm9tIHBhdGhsaWIgaW1wb3J0IFBhdGgKaW1wb3J0IHJlCmltcG9ydCBzaHV0aWwKaW1wb3J0IHNpZ25hbAppbXBvcnQgc3VicHJvY2VzcwppbXBvcnQgc3lzCmltcG9ydCB0ZW1wZmlsZQppbXBvcnQgdGhyZWFkaW5nCmltcG9ydCB0aW1lCmZyb20gaHR0cC5zZXJ2ZXIgaW1wb3J0IEJhc2VIVFRQUmVxdWVzdEhhbmRsZXIsIFRocmVhZGluZ0hUVFBTZXJ2ZXIKClNLSUxMID0gUGF0aChvcy5lbnZpcm9uLmdldCgiRU5FUkdZX09SSUdJTkFMX1NLSUxMIiwgIi9vcHQvZW5lcmd5LXNraWxsIikpClBSSVZBVEUgPSBQYXRoKG9zLmVudmlyb24uZ2V0KCJFTkVSR1lfUFJJVkFURV9ST09UIiwgIi9wcml2YXRlIikpCkZFRUQgPSBQYXRoKG9zLmVudmlyb24uZ2V0KCJFTkVSR1lfTUFSS0VUX0ZFRUQiLCAiL2ZlZWQiKSkKQ1JFREVOVElBTCA9IFBhdGgoIi9ydW4vZW5lcmd5LWNyZWRlbnRpYWwvY3JlZGVudGlhbC5qc29uIikKSE9TVCwgUE9SVCA9ICJpcDkxLmpreC5jb20uY24iLCA2Njg4ClRaID0gZHQudGltZXpvbmUoZHQudGltZWRlbHRhKGhvdXJzPTgpKQpMT0NLID0gdGhyZWFkaW5nLkxvY2soKQpTVEFURSA9IHt9CkFDVElWRSA9IE5vbmUKCiMgTmV0d29yayBhZG1pc3Npb24gYmVsb25ncyB0byB0aGUgcHJvY2VzcyB3cmFwcGVyLCBub3QgdG8gYSBtb2RpZmllZCBzdXBwbGllciBtb2R1bGUuClJVTk5FUiA9IHIiIiIKaW1wb3J0IGNvbnRleHRsaWIsaW8sanNvbixvcyxzb2NrZXQsc3lzCmhvc3Q9J2lwOTEuamt4LmNvbS5jbic7IHBvcnQ9NjY4OApvdXQ9eydzdGF0dXMnOidJTkNPTVBMRVRFJywnY29ubmVjdGlvbnMnOjB9CnRyeToKIGFkZHJlc3Nlcz17eFs0XVswXSBmb3IgeCBpbiBzb2NrZXQuZ2V0YWRkcmluZm8oaG9zdCxwb3J0LHR5cGU9c29ja2V0LlNPQ0tfU1RSRUFNKX0KIGRlZiBndWFyZChldmVudCxhcmdzKToKICBpZiBldmVudD09J3NvY2tldC5nZXRhZGRyaW5mbyc6CiAgIG5hbWU9YXJnc1swXS5kZWNvZGUoJ2FzY2lpJykgaWYgaXNpbnN0YW5jZShhcmdzWzBdLGJ5dGVzKSBlbHNlIGFyZ3NbMF0KICAgaWYgbmFtZSE9aG9zdCBvciBhcmdzWzFdIG5vdCBpbiAocG9ydCxzdHIocG9ydCkpOnJhaXNlIFJ1bnRpbWVFcnJvcignTkVUV09SS19UQVJHRVRfUkVGVVNFRCcpCiAgZWxpZiBldmVudD09J3NvY2tldC5jb25uZWN0JzoKICAgYWRkcmVzcz1hcmdzWzFdCiAgIGlmIG5vdCBpc2luc3RhbmNlKGFkZHJlc3MsdHVwbGUpIG9yIGFkZHJlc3NbMF0gbm90IGluIGFkZHJlc3NlcyBvciBhZGRyZXNzWzFdIT1wb3J0OnJhaXNlIFJ1bnRpbWVFcnJvcignTkVUV09SS19UQVJHRVRfUkVGVVNFRCcpCiAgIGlmIG91dFsnY29ubmVjdGlvbnMnXT49aW50KHN5cy5hcmd2WzNdKTpyYWlzZSBSdW50aW1lRXJyb3IoJ05FVFdPUktfQlVER0VUJykKICAgb3V0Wydjb25uZWN0aW9ucyddKz0xCiBzeXMuYWRkYXVkaXRob29rKGd1YXJkKQogc3lzLnBhdGguaW5zZXJ0KDAsc3lzLmFyZ3ZbMV0pCiBpbXBvcnQgZmV0Y2hfbWFya2V0X2hpc3RvcnkgYXMgb3JpZ2luYWwKIHN5cy5hcmd2PVsnZmV0Y2hfbWFya2V0X2hpc3RvcnkucHknLCctLWRhdGUnLHN5cy5hcmd2WzJdXQogd2l0aCBjb250ZXh0bGliLnJlZGlyZWN0X3N0ZG91dChpby5TdHJpbmdJTygpKSxjb250ZXh0bGliLnJlZGlyZWN0X3N0ZGVycihpby5TdHJpbmdJTygpKTpvcmlnaW5hbC5tYWluKCkKIG91dFsnc3RhdHVzJ109J09SSUdJTkFMX01BUktFVF9DT01QTEVURScKZXhjZXB0IEV4Y2VwdGlvbiBhcyBleGM6b3V0WydlcnJvcl9jbGFzcyddPXR5cGUoZXhjKS5fX25hbWVfXwpwcmludChqc29uLmR1bXBzKG91dCxzb3J0X2tleXM9VHJ1ZSkpCiIiIgoKCmRlZiBsb2FkX2VudHJ5KCk6CiAgICBzcGVjID0gaW1wb3J0bGliLnV0aWwuc3BlY19mcm9tX2ZpbGVfbG9jYXRpb24oCiAgICAgICAgImVuZXJneV9lbnRyeSIsIFNLSUxMIC8gInNjcmlwdHMvZW50cnkucHkiCiAgICApCiAgICBlbnRyeSA9IGltcG9ydGxpYi51dGlsLm1vZHVsZV9mcm9tX3NwZWMoc3BlYykKICAgIHNwZWMubG9hZGVyLmV4ZWNfbW9kdWxlKGVudHJ5KQogICAgZW50cnkub3JpZ2luYWxfZmlsZXMoKQogICAgcmV0dXJuIGVudHJ5CgoKZGVmIGNyZWRlbnRpYWwoKToKICAgICMgS3ViZXJuZXRlcyBTZWNyZXQgcHJvamVjdGlvbiB1c2VzIC4uZGF0YSBzeW1saW5rczsgcmVzb2x2ZSBvbmx5IGluc2lkZSBpdHMgZml4ZWQgbW91bnQuCiAgICBhY3R1YWwgPSBDUkVERU5USUFMLnJlc29sdmUoc3RyaWN0PVRydWUpCiAgICBpZiBQYXRoKCIvcnVuL2VuZXJneS1jcmVkZW50aWFsIikgbm90IGluIGFjdHVhbC5wYXJlbnRzOgogICAgICAgIHJhaXNlIFZhbHVlRXJyb3IoIkNSRURFTlRJQUxfTU9VTlRfRVNDQVBFIikKICAgIHN0ID0gYWN0dWFsLnN0YXQoKQogICAgaWYgc3Quc3Rfc2l6ZSA+IDQwOTYgb3Igc3Quc3RfbW9kZSAmIDBvMDc3OgogICAgICAgIHJhaXNlIFZhbHVlRXJyb3IoIkNSRURFTlRJQUxfTU9ERSIpCiAgICB2YWx1ZSA9IGpzb24ubG9hZHMoYWN0dWFsLnJlYWRfYnl0ZXMoKSkKICAgIGlmICgKICAgICAgICBzZXQodmFsdWUpICE9IHsiSktYX0FQSV9FTkRQT0lOVCIsICJKS1hfQVBJX1BBU1NXT1JEIn0KICAgICAgICBvciB2YWx1ZVsiSktYX0FQSV9FTkRQT0lOVCJdICE9ICJodHRwOi8vaXA5MS5qa3guY29tLmNuOjY2ODgvamt4X2VlY2MuYXNweCIKICAgICk6CiAgICAgICAgcmFpc2UgVmFsdWVFcnJvcigiQ1JFREVOVElBTF9TQ0hFTUEiKQogICAgaWYgKAogICAgICAgIG5vdCBpc2luc3RhbmNlKHZhbHVlWyJKS1hfQVBJX1BBU1NXT1JEIl0sIHN0cikKICAgICAgICBvciBub3QgMSA8PSBsZW4odmFsdWVbIkpLWF9BUElfUEFTU1dPUkQiXSkgPD0gMTAyNAogICAgKToKICAgICAgICByYWlzZSBWYWx1ZUVycm9yKCJDUkVERU5USUFMX1ZBTFVFIikKICAgIHJldHVybiB2YWx1ZQoKCmRlZiBkYXRlX3dpbmRvdyhkYXkpOgogICAgcGFyc2VkID0gZHQuZGF0ZS5mcm9taXNvZm9ybWF0KGRheSkKICAgIHRvZGF5ID0gZHQuZGF0ZXRpbWUubm93KFRaKS5kYXRlKCkKICAgIGlmIHBhcnNlZC5pc29mb3JtYXQoKSAhPSBkYXkgb3Igbm90IHRvZGF5IC0gZHQudGltZWRlbHRhKGRheXM9NykgPD0gcGFyc2VkIDw9IHRvZGF5OgogICAgICAgIHJhaXNlIFZhbHVlRXJyb3IoIkRBVEVfV0lORE9XIikKICAgIHJldHVybiBwYXJzZWQKCgpkZWYgcHVibGlzaF9jYWNoZShlbmdpbmUsIGtleSwgZW50cnksIHRhcmdldF9kYXkpOgogICAgIyBGaXhlZCByZXNwb25zZSBmaWVsZHMgb25seS4gUHJvdmUgdGhlIG9yaWdpbmFsIG5vcm1hbGl6ZXIgc2VlcyBpZGVudGljYWwgb2JzZXJ2YXRpb25zLgogICAgc3BlYyA9IGltcG9ydGxpYi51dGlsLnNwZWNfZnJvbV9maWxlX2xvY2F0aW9uKAogICAgICAgICJlbmVyZ3lfb3JpZ2luYWxfZmV0Y2giLCBlbmdpbmUgLyAiYXBwL2ZldGNoX21hcmtldF9oaXN0b3J5LnB5IgogICAgKQogICAgcHJpb3JfcGF0aCA9IGxpc3Qoc3lzLnBhdGgpCiAgICB0cnk6CiAgICAgICAgc3lzLnBhdGguaW5zZXJ0KDAsIHN0cihlbmdpbmUgLyAiYXBwIikpCiAgICAgICAgbW9kdWxlID0gaW1wb3J0bGliLnV0aWwubW9kdWxlX2Zyb21fc3BlYyhzcGVjKQogICAgICAgIHNwZWMubG9hZGVyLmV4ZWNfbW9kdWxlKG1vZHVsZSkKICAgICAgICBmb3IgcGF0aCBpbiBzb3J0ZWQoKGVuZ2luZSAvICJhcHAvc291cmNlcyIpLmdsb2IoImpreF9uZXdfPz8/Py0/Py0/Py5qc29uIikpOgogICAgICAgICAgICBkYXkgPSBwYXRoLnN0ZW0ucmVtb3ZlcHJlZml4KCJqa3hfbmV3XyIpCiAgICAgICAgICAgIHJhdyA9IGVudHJ5LnJlYWQocGF0aCwgMjYyMTQ0KQogICAgICAgICAgICBwYXlsb2FkID0ganNvbi5sb2FkcyhyYXcpCiAgICAgICAgICAgIG9yaWdpbmFsID0gbW9kdWxlLm5vcm1hbGl6ZShwYXlsb2FkLCBkYXkpCiAgICAgICAgICAgICMgQSB0YXJnZXQgd2l0aCBubyBxdW90ZXMgbXVzdCBiZSBmZXRjaGVkIGFnYWluIGFmdGVyIHRoZSBjb29sZG93biwKICAgICAgICAgICAgIyBub3QgcGVybWFuZW50bHkgZnJvemVuIGJ5IHRoZSB1cHN0cmVhbSBjYWNoZS1maXJzdCBiZWhhdmlvci4KICAgICAgICAgICAgaWYgZGF5ID09IHRhcmdldF9kYXkgYW5kIGFueShyWyJzdGF0dXMiXSAhPSAiYXZhaWxhYmxlIiBmb3IgciBpbiBvcmlnaW5hbCk6CiAgICAgICAgICAgICAgICBjb250aW51ZQogICAgICAgICAgICBwdWJsaWMgPSB7CiAgICAgICAgICAgICAgICAiZXJyY29kZSI6IDAsCiAgICAgICAgICAgICAgICAiZGF0YSI6IFsKICAgICAgICAgICAgICAgICAgICB7CiAgICAgICAgICAgICAgICAgICAgICAgIGs6IHJba10KICAgICAgICAgICAgICAgICAgICAgICAgZm9yIGsgaW4gKCJjb2RlX25hbWUiLCAidmFsdWVfZGF0ZSIsICJjbG9zZSIsICJ1bml0IiwgIml0ZW0iKQogICAgICAgICAgICAgICAgICAgIH0KICAgICAgICAgICAgICAgICAgICBmb3IgciBpbiBwYXlsb2FkWyJkYXRhIl0KICAgICAgICAgICAgICAgIF0sCiAgICAgICAgICAgIH0KICAgICAgICAgICAgaWYgbW9kdWxlLm5vcm1hbGl6ZShwdWJsaWMsIGRheSkgIT0gb3JpZ2luYWw6CiAgICAgICAgICAgICAgICByYWlzZSBWYWx1ZUVycm9yKCJDQUNIRV9QUk9KRUNUSU9OX0NIQU5HRURfT0JTRVJWQVRJT05TIikKICAgICAgICAgICAgZW5jb2RlZCA9ICgKICAgICAgICAgICAgICAgIGpzb24uZHVtcHMocHVibGljLCBlbnN1cmVfYXNjaWk9RmFsc2UsIGFsbG93X25hbj1GYWxzZSkgKyAiXG4iCiAgICAgICAgICAgICkuZW5jb2RlKCJ1dGYtOCIpCiAgICAgICAgICAgIGlmIGtleS5lbmNvZGUoInV0Zi04IikgaW4gZW5jb2RlZCBvciBsZW4oZW5jb2RlZCkgPiAyNjIxNDQ6CiAgICAgICAgICAgICAgICByYWlzZSBWYWx1ZUVycm9yKCJDQUNIRV9QUklWQVRFX09VVFBVVF9SRUpFQ1RFRCIpCiAgICAgICAgICAgIHRhcmdldCA9IEZFRUQgLyBwYXRoLm5hbWUKICAgICAgICAgICAgaWYgdGFyZ2V0LmV4aXN0cygpOgogICAgICAgICAgICAgICAgaWYgZW50cnkucmVhZCh0YXJnZXQsIDI2MjE0NCkgIT0gZW5jb2RlZDoKICAgICAgICAgICAgICAgICAgICByYWlzZSBWYWx1ZUVycm9yKCJQVUJMSVNIRURfQ0FDSEVfQ09ORkxJQ1QiKQogICAgICAgICAgICAgICAgY29udGludWUKICAgICAgICAgICAgZmQsIG5hbWUgPSB0ZW1wZmlsZS5ta3N0ZW1wKHByZWZpeD0iLnN0YWdlLSIsIGRpcj1GRUVEKQogICAgICAgICAgICB0cnk6CiAgICAgICAgICAgICAgICB3aXRoIG9zLmZkb3BlbihmZCwgIndiIikgYXMgc3RyZWFtOgogICAgICAgICAgICAgICAgICAgIHN0cmVhbS53cml0ZShlbmNvZGVkKQogICAgICAgICAgICAgICAgICAgIHN0cmVhbS5mbHVzaCgpCiAgICAgICAgICAgICAgICAgICAgb3MuZnN5bmMoc3RyZWFtLmZpbGVubygpKQogICAgICAgICAgICAgICAgb3MuY2htb2QobmFtZSwgMG82NDQpCiAgICAgICAgICAgICAgICBvcy5saW5rKG5hbWUsIHRhcmdldCkKICAgICAgICAgICAgZmluYWxseToKICAgICAgICAgICAgICAgIG9zLnVubGluayhuYW1lKQogICAgZmluYWxseToKICAgICAgICBzeXMucGF0aFs6XSA9IHByaW9yX3BhdGgKCgpkZWYgY29sbGVjdChkYXkpOgogICAgZ2xvYmFsIEFDVElWRQogICAgcmVzdWx0ID0geyJzdGF0dXMiOiAiSU5DT01QTEVURSIsICJkYXRhX2RhdGUiOiBkYXksICJjcmVkZW50aWFsc19leHBvcnRlZCI6IEZhbHNlfQogICAgdHJ5OgogICAgICAgIGVudHJ5ID0gbG9hZF9lbnRyeSgpCiAgICAgICAga2V5ID0gY3JlZGVudGlhbCgpCiAgICAgICAgd2l0aCB0ZW1wZmlsZS5UZW1wb3JhcnlEaXJlY3RvcnkocHJlZml4PSJvcmlnaW5hbC0iLCBkaXI9UFJJVkFURSkgYXMgd29yazoKICAgICAgICAgICAgZW5naW5lID0gUGF0aCh3b3JrKSAvICJlbmdpbmUiCiAgICAgICAgICAgIHNodXRpbC5jb3B5dHJlZShTS0lMTCAvICJ1cHN0cmVhbSIsIGVuZ2luZSkKICAgICAgICAgICAgZXhwZWN0ZWQgPSBlbnRyeS5vcmlnaW5hbF9maWxlcygpCiAgICAgICAgICAgIGVudHJ5LnZlcmlmeV90cmVlKGVuZ2luZSwgZXhwZWN0ZWQsIEZhbHNlKQogICAgICAgICAgICBmb3IgcGF0aCBpbiBGRUVELmdsb2IoImpreF9uZXdfPz8/Py0/Py0/Py5qc29uIik6CiAgICAgICAgICAgICAgICBpZiBub3QgcmUuZnVsbG1hdGNoKHIiamt4X25ld19cZHs0fS1cZHsyfS1cZHsyfVwuanNvbiIsIHBhdGgubmFtZSk6CiAgICAgICAgICAgICAgICAgICAgcmFpc2UgVmFsdWVFcnJvcigiQ0FDSEVfRklMRU5BTUUiKQogICAgICAgICAgICAgICAgKGVuZ2luZSAvICJhcHAvc291cmNlcyIgLyBwYXRoLm5hbWUpLndyaXRlX2J5dGVzKAogICAgICAgICAgICAgICAgICAgIGVudHJ5LnJlYWQocGF0aCwgMjYyMTQ0KQogICAgICAgICAgICAgICAgKQogICAgICAgICAgICBlbnYgPSB7CiAgICAgICAgICAgICAgICBrOiBvcy5lbnZpcm9uW2tdCiAgICAgICAgICAgICAgICBmb3IgayBpbiAoIlBBVEgiLCAiSE9NRSIsICJMQU5HIiwgIkxEX0xJQlJBUllfUEFUSCIpCiAgICAgICAgICAgICAgICBpZiBrIGluIG9zLmVudmlyb24KICAgICAgICAgICAgfQogICAgICAgICAgICBlbnYudXBkYXRlKGtleSkKICAgICAgICAgICAgZW52LnVwZGF0ZSgKICAgICAgICAgICAgICAgIE5PX1BST1hZPUhPU1QsCiAgICAgICAgICAgICAgICBub19wcm94eT1IT1NULAogICAgICAgICAgICAgICAgUFlUSE9OVVRGOD0iMSIsCiAgICAgICAgICAgICAgICBQWVRIT05ET05UV1JJVEVCWVRFQ09ERT0iMSIsCiAgICAgICAgICAgICkKICAgICAgICAgICAgd2l0aCB0ZW1wZmlsZS5UZW1wb3JhcnlGaWxlKCkgYXMgbG9nOgogICAgICAgICAgICAgICAgY2hpbGQgPSBzdWJwcm9jZXNzLlBvcGVuKAogICAgICAgICAgICAgICAgICAgIFsKICAgICAgICAgICAgICAgICAgICAgICAgc3lzLmV4ZWN1dGFibGUsCiAgICAgICAgICAgICAgICAgICAgICAgICItSSIsCiAgICAgICAgICAgICAgICAgICAgICAgICItQiIsCiAgICAgICAgICAgICAgICAgICAgICAgICItYyIsCiAgICAgICAgICAgICAgICAgICAgICAgIFJVTk5FUiwKICAgICAgICAgICAgICAgICAgICAgICAgc3RyKGVuZ2luZSAvICJhcHAiKSwKICAgICAgICAgICAgICAgICAgICAgICAgZGF5LAogICAgICAgICAgICAgICAgICAgICAgICAiMTg4IiwKICAgICAgICAgICAgICAgICAgICBdLAogICAgICAgICAgICAgICAgICAgIGVudj1lbnYsCiAgICAgICAgICAgICAgICAgICAgc3Rkb3V0PWxvZywKICAgICAgICAgICAgICAgICAgICBzdGRlcnI9bG9nLAogICAgICAgICAgICAgICAgICAgIHN0YXJ0X25ld19zZXNzaW9uPVRydWUsCiAgICAgICAgICAgICAgICApCiAgICAgICAgICAgICAgICB0cnk6CiAgICAgICAgICAgICAgICAgICAgY2hpbGQud2FpdCh0aW1lb3V0PTYwMCkKICAgICAgICAgICAgICAgIGV4Y2VwdCBzdWJwcm9jZXNzLlRpbWVvdXRFeHBpcmVkOgogICAgICAgICAgICAgICAgICAgIG9zLmtpbGxwZyhjaGlsZC5waWQsIHNpZ25hbC5TSUdLSUxMKQogICAgICAgICAgICAgICAgICAgIGNoaWxkLndhaXQodGltZW91dD0xMCkKICAgICAgICAgICAgICAgICAgICByYWlzZSBWYWx1ZUVycm9yKCJDT0xMRUNUT1JfVElNRU9VVCIpIGZyb20gTm9uZQogICAgICAgICAgICAgICAgaWYgY2hpbGQucmV0dXJuY29kZSAhPSAwIG9yIGxvZy50ZWxsKCkgPiA4MTkyOgogICAgICAgICAgICAgICAgICAgIHJhaXNlIFZhbHVlRXJyb3IoIkNPTExFQ1RPUl9QUk9DRVNTX0ZBSUxFRCIpCiAgICAgICAgICAgICAgICBsb2cuc2VlaygwKQogICAgICAgICAgICAgICAgb2JzZXJ2YXRpb24gPSBqc29uLmxvYWRzKGxvZy5yZWFkKCkpCiAgICAgICAgICAgIGVudHJ5LnZlcmlmeV90cmVlKGVuZ2luZSwgZXhwZWN0ZWQsIFRydWUpCiAgICAgICAgICAgIHJlc3VsdFsidGFyZ2V0X3Jlc3BvbnNlX3ZhbGlkYXRlZCJdID0gKAogICAgICAgICAgICAgICAgZW5naW5lIC8gImFwcC9zb3VyY2VzIiAvICgiamt4X25ld18iICsgZGF5ICsgIi5qc29uIikKICAgICAgICAgICAgKS5pc19maWxlKCkKICAgICAgICAgICAgIyBSZXRhaW4gdmVyaWZpZWQgaGlzdG9yaWNhbCBjYWNoZSBldmVuIGlmIHRoZSB0YXJnZXQgZGF5IGlzIG5vdCByZWFkeS4KICAgICAgICAgICAgcHVibGlzaF9jYWNoZShlbmdpbmUsIGtleVsiSktYX0FQSV9QQVNTV09SRCJdLCBlbnRyeSwgZGF5KQogICAgICAgICAgICByZXN1bHRbImNvbm5lY3Rpb25zIl0gPSBvYnNlcnZhdGlvbi5nZXQoImNvbm5lY3Rpb25zIikKICAgICAgICAgICAgcmVzdWx0WyJzdGF0dXMiXSA9ICgKICAgICAgICAgICAgICAgICJSRUFEWSIKICAgICAgICAgICAgICAgIGlmIG9ic2VydmF0aW9uLmdldCgic3RhdHVzIikgPT0gIk9SSUdJTkFMX01BUktFVF9DT01QTEVURSIKICAgICAgICAgICAgICAgIGVsc2UgIldBSVRfREFUQSIKICAgICAgICAgICAgKQogICAgICAgIGVudHJ5Lm9yaWdpbmFsX2ZpbGVzKCkKICAgIGV4Y2VwdCBFeGNlcHRpb24gYXMgZXhjOgogICAgICAgIHJlc3VsdFsiZXJyb3JfY2xhc3MiXSA9IHR5cGUoZXhjKS5fX25hbWVfXwogICAgZmluYWxseToKICAgICAgICB3aXRoIExPQ0s6CiAgICAgICAgICAgIFNUQVRFW2RheV0gPSBkaWN0KHJlc3VsdCwgcmV0cnlfYWZ0ZXJfZXBvY2g9dGltZS50aW1lKCkgKyAzMDApCiAgICAgICAgICAgIEFDVElWRSA9IE5vbmUKCgpjbGFzcyBIYW5kbGVyKEJhc2VIVFRQUmVxdWVzdEhhbmRsZXIpOgogICAgZGVmIHNldHVwKHNlbGYpOgogICAgICAgIHN1cGVyKCkuc2V0dXAoKQogICAgICAgIHNlbGYuY29ubmVjdGlvbi5zZXR0aW1lb3V0KDEwKQoKICAgIGRlZiBsb2dfbWVzc2FnZShzZWxmLCAqYXJncyk6CiAgICAgICAgcGFzcwoKICAgIGRlZiByZXBseShzZWxmLCBjb2RlLCBib2R5KToKICAgICAgICBkYXRhID0ganNvbi5kdW1wcyhib2R5LCBzb3J0X2tleXM9VHJ1ZSkuZW5jb2RlKCJ1dGYtOCIpCiAgICAgICAgc2VsZi5zZW5kX3Jlc3BvbnNlKGNvZGUpCiAgICAgICAgc2VsZi5zZW5kX2hlYWRlcigiQ29udGVudC1UeXBlIiwgImFwcGxpY2F0aW9uL2pzb24iKQogICAgICAgIHNlbGYuc2VuZF9oZWFkZXIoIkNvbnRlbnQtTGVuZ3RoIiwgc3RyKGxlbihkYXRhKSkpCiAgICAgICAgc2VsZi5zZW5kX2hlYWRlcigiQ2FjaGUtQ29udHJvbCIsICJuby1zdG9yZSIpCiAgICAgICAgc2VsZi5lbmRfaGVhZGVycygpCiAgICAgICAgc2VsZi53ZmlsZS53cml0ZShkYXRhKQoKICAgIGRlZiBkb19HRVQoc2VsZik6CiAgICAgICAgaWYgc2VsZi5wYXRoID09ICIvaGVhbHRoIjoKICAgICAgICAgICAgcmV0dXJuIHNlbGYucmVwbHkoMjAwLCB7InN0YXR1cyI6ICJVUCJ9KQogICAgICAgIHJldHVybiBzZWxmLnJlcGx5KDQwNCwgeyJzdGF0dXMiOiAiTk9UX0ZPVU5EIn0pCgogICAgZGVmIGRvX1BPU1Qoc2VsZik6CiAgICAgICAgZ2xvYmFsIEFDVElWRQogICAgICAgIG1hdGNoID0gcmUuZnVsbG1hdGNoKHIiL3ByZXBhcmUvKFxkezR9LVxkezJ9LVxkezJ9KSIsIHNlbGYucGF0aCkKICAgICAgICBpZiAoCiAgICAgICAgICAgIG5vdCBtYXRjaAogICAgICAgICAgICBvciBzZWxmLmhlYWRlcnMuZ2V0KCJUcmFuc2Zlci1FbmNvZGluZyIpCiAgICAgICAgICAgIG9yIHNlbGYuaGVhZGVycy5nZXQoIkNvbnRlbnQtTGVuZ3RoIiwgIjAiKSAhPSAiMCIKICAgICAgICApOgogICAgICAgICAgICByZXR1cm4gc2VsZi5yZXBseSg0MDAsIHsic3RhdHVzIjogIklOVkFMSURfUkVRVUVTVCJ9KQogICAgICAgIGRheSA9IG1hdGNoLmdyb3VwKDEpCiAgICAgICAgdHJ5OgogICAgICAgICAgICBkYXRlX3dpbmRvdyhkYXkpCiAgICAgICAgZXhjZXB0IFZhbHVlRXJyb3I6CiAgICAgICAgICAgIHJldHVybiBzZWxmLnJlcGx5KDQwMCwgeyJzdGF0dXMiOiAiREFURV9XSU5ET1cifSkKICAgICAgICB3aXRoIExPQ0s6CiAgICAgICAgICAgIGZvciBvbGQgaW4gbGlzdChTVEFURSk6CiAgICAgICAgICAgICAgICBpZiAoCiAgICAgICAgICAgICAgICAgICAgb2xkCiAgICAgICAgICAgICAgICAgICAgPCAoZHQuZGF0ZXRpbWUubm93KFRaKS5kYXRlKCkgLSBkdC50aW1lZGVsdGEoZGF5cz03KSkuaXNvZm9ybWF0KCkKICAgICAgICAgICAgICAgICk6CiAgICAgICAgICAgICAgICAgICAgU1RBVEUucG9wKG9sZCwgTm9uZSkKICAgICAgICAgICAgY3VycmVudCA9IFNUQVRFLmdldChkYXkpCiAgICAgICAgICAgIGlmIGN1cnJlbnQgYW5kICgKICAgICAgICAgICAgICAgIGN1cnJlbnRbInN0YXR1cyJdID09ICJSRUFEWSIKICAgICAgICAgICAgICAgIG9yIGN1cnJlbnRbInJldHJ5X2FmdGVyX2Vwb2NoIl0gPiB0aW1lLnRpbWUoKQogICAgICAgICAgICApOgogICAgICAgICAgICAgICAgcmV0dXJuIHNlbGYucmVwbHkoCiAgICAgICAgICAgICAgICAgICAgMjAwIGlmIGN1cnJlbnRbInN0YXR1cyJdID09ICJSRUFEWSIgZWxzZSAyMDIsCiAgICAgICAgICAgICAgICAgICAge2s6IHYgZm9yIGssIHYgaW4gY3VycmVudC5pdGVtcygpIGlmIGsgIT0gInJldHJ5X2FmdGVyX2Vwb2NoIn0sCiAgICAgICAgICAgICAgICApCiAgICAgICAgICAgIGlmIEFDVElWRSBpcyBub3QgTm9uZToKICAgICAgICAgICAgICAgIHJldHVybiBzZWxmLnJlcGx5KDIwMiwgeyJzdGF0dXMiOiAiQ09MTEVDVElORyIsICJkYXRhX2RhdGUiOiBkYXl9KQogICAgICAgICAgICBBQ1RJVkUgPSBkYXkKICAgICAgICAgICAgdGhyZWFkaW5nLlRocmVhZCh0YXJnZXQ9Y29sbGVjdCwgYXJncz0oZGF5LCksIGRhZW1vbj1UcnVlKS5zdGFydCgpCiAgICAgICAgcmV0dXJuIHNlbGYucmVwbHkoMjAyLCB7InN0YXR1cyI6ICJDT0xMRUNUSU5HIiwgImRhdGFfZGF0ZSI6IGRheX0pCgoKY2xhc3MgQm91bmRlZFNlcnZlcihUaHJlYWRpbmdIVFRQU2VydmVyKToKICAgIGRhZW1vbl90aHJlYWRzID0gVHJ1ZQogICAgcmVxdWVzdF9xdWV1ZV9zaXplID0gMTYKICAgIHNsb3RzID0gdGhyZWFkaW5nLkJvdW5kZWRTZW1hcGhvcmUoMTYpCgogICAgZGVmIHByb2Nlc3NfcmVxdWVzdChzZWxmLCByZXF1ZXN0LCBjbGllbnRfYWRkcmVzcyk6CiAgICAgICAgaWYgbm90IHNlbGYuc2xvdHMuYWNxdWlyZShibG9ja2luZz1GYWxzZSk6CiAgICAgICAgICAgIHNlbGYuc2h1dGRvd25fcmVxdWVzdChyZXF1ZXN0KQogICAgICAgICAgICByZXR1cm4KICAgICAgICB0cnk6CiAgICAgICAgICAgIHN1cGVyKCkucHJvY2Vzc19yZXF1ZXN0KHJlcXVlc3QsIGNsaWVudF9hZGRyZXNzKQogICAgICAgIGV4Y2VwdCBCYXNlRXhjZXB0aW9uOgogICAgICAgICAgICBzZWxmLnNsb3RzLnJlbGVhc2UoKQogICAgICAgICAgICByYWlzZQoKICAgIGRlZiBwcm9jZXNzX3JlcXVlc3RfdGhyZWFkKHNlbGYsIHJlcXVlc3QsIGNsaWVudF9hZGRyZXNzKToKICAgICAgICB0cnk6CiAgICAgICAgICAgIHN1cGVyKCkucHJvY2Vzc19yZXF1ZXN0X3RocmVhZChyZXF1ZXN0LCBjbGllbnRfYWRkcmVzcykKICAgICAgICBmaW5hbGx5OgogICAgICAgICAgICBzZWxmLnNsb3RzLnJlbGVhc2UoKQoKCmRlZiBtYWluKCk6CiAgICBQUklWQVRFLm1rZGlyKG1vZGU9MG83MDAsIHBhcmVudHM9VHJ1ZSwgZXhpc3Rfb2s9VHJ1ZSkKICAgIEZFRUQubWtkaXIocGFyZW50cz1UcnVlLCBleGlzdF9vaz1UcnVlKQogICAgaWYgUFJJVkFURS5pc19zeW1saW5rKCkgb3IgRkVFRC5pc19zeW1saW5rKCk6CiAgICAgICAgcmFpc2UgVmFsdWVFcnJvcigiTU9VTlRfU1lNTElOSyIpCiAgICBsb2FkX2VudHJ5KCkKICAgIGNyZWRlbnRpYWwoKQogICAgc2VydmVyID0gQm91bmRlZFNlcnZlcigoIjAuMC4wLjAiLCA4NzY2KSwgSGFuZGxlcikKICAgIHNlcnZlci5zZXJ2ZV9mb3JldmVyKCkKCgppZiBfX25hbWVfXyA9PSAiX19tYWluX18iOgogICAgbWFpbigpCg=="
ROOT = Path("/home/cnooc")
PRIOR_REPORT_DIR = Path("/var/lib/portal-energy-market-skill-install-20261109-r2")
PRIOR_REPORT_SHA = "99813bbd7301b53f56fe93d92c9658bfde3abb6e30d835e7c4dd46a68b716574"
PRIOR_OPERATION = "dfb4edf4280a449db774981d2f703a89"
PRIOR_POD = "energy-maint-dfb4edf4280a"
PRIOR_POD_UID = "558e905d-cac7-4f7b-9760-7fc10e0284d2"
REPORT_DIR = Path("/var/lib/portal-energy-market-skill-resume-20261109-r1")
BASELINE = {
    "gateway": (
        411,
        "deerflow.local/deer-flow-gateway@sha256:d13c5343702c28316bee686292ab2ff76edb0151baf17af3f198ce203df992ee",
    ),
    "frontend": (
        243,
        "deerflow.local/deer-flow-frontend@sha256:e11221108f3b224ccd00ac188d625e389ae72f20f7ab7f71900fb66a2d00cafa",
    ),
    "provisioner": (
        86,
        "deerflow.local/deer-flow-provisioner:20261005-prod-node-pool-r1",
    ),
}


def require(ok, code):
    if not ok:
        raise ValueError(code)


def check_host():
    require(
        os.name == "posix"
        and os.geteuid() == 0
        and os.uname().nodename.split(".")[0] == "datax-01",
        "DATAX01_ROOT_REQUIRED",
    )


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_file(path, limit):
    require(
        not any(p.is_symlink() for p in [path] + list(path.parents)), "FILE_SYMLINK"
    )
    fd = os.open(
        str(path),
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
    )
    try:
        before = os.fstat(fd)
        require(
            stat.S_ISREG(before.st_mode) and before.st_size <= limit,
            "FILE_BOUND_OR_TYPE",
        )
        with os.fdopen(fd, "rb", closefd=False) as f:
            raw = f.read(limit + 1)
        after = os.fstat(fd)
        require(
            len(raw) <= limit
            and (before.st_size, before.st_mtime_ns)
            == (after.st_size, after.st_mtime_ns),
            "FILE_CHANGED",
        )
        return raw, before
    finally:
        os.close(fd)


def stable_deployment(d):
    s, t = d["spec"], d.get("status", {})
    desired = s.get("replicas", 1)
    return (
        not s.get("paused", False)
        and t.get("observedGeneration") == d["metadata"]["generation"]
        and all(
            t.get(k, 0) == desired
            for k in (
                "replicas",
                "updatedReplicas",
                "readyReplicas",
                "availableReplicas",
            )
        )
        and t.get("terminatingReplicas", 0) == 0
    )


def identity(d):
    return {
        "uid": d["metadata"]["uid"],
        "generation": d["metadata"].get("generation"),
        "spec_sha256": sha(
            json.dumps(d["spec"], sort_keys=True, separators=(",", ":")).encode()
        ),
    }


def build_resources(node_uid, operation, maintenance_operation=None):
    label = {"app": DEPLOYMENT}
    mount = [
        {
            "name": "skills",
            "mountPath": "/opt/energy-skill",
            "subPath": "public/energy-market-daily",
            "readOnly": True,
        },
        {
            "name": "skills",
            "mountPath": "/feed",
            "subPath": "public/energy-market-data",
        },
        {"name": "private", "mountPath": "/private"},
        {"name": "tmp", "mountPath": "/tmp"},
        {"name": "code", "mountPath": "/opt/collector", "readOnly": True},
        {"name": "credential", "mountPath": "/run/energy-credential", "readOnly": True},
    ]
    volumes = [
        {"name": "skills", "persistentVolumeClaim": {"claimName": PVC}},
        {"name": "private", "emptyDir": {"sizeLimit": "128Mi"}},
        {"name": "tmp", "emptyDir": {"sizeLimit": "64Mi"}},
        {"name": "code", "configMap": {"name": CONFIG}},
        {"name": "credential", "secret": {"secretName": SECRET, "defaultMode": 256}},
    ]
    container = {
        "name": "collector",
        "image": IMAGE,
        "imagePullPolicy": "Never",
        "command": ["python3"],
        "args": [
            "-I",
            "-B",
            "-u",
            "/opt/collector/energy_market_collector.py",
        ],
        "env": [
            {"name": "HOME", "value": "/private"},
            {"name": "PYTHONUTF8", "value": "1"},
        ],
        "ports": [{"name": "http", "containerPort": 8766}],
        "volumeMounts": mount,
        "securityContext": {
            "runAsUser": 0,
            "readOnlyRootFilesystem": True,
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"]},
        },
        "resources": {
            "requests": {"cpu": "100m", "memory": "128Mi"},
            "limits": {"cpu": "1", "memory": "1Gi"},
        },
        "readinessProbe": {
            "httpGet": {"path": "/health", "port": 8766},
            "periodSeconds": 5,
            "timeoutSeconds": 2,
        },
        "livenessProbe": {
            "httpGet": {"path": "/health", "port": 8766},
            "initialDelaySeconds": 30,
            "periodSeconds": 20,
            "timeoutSeconds": 3,
        },
    }
    deployment = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": DEPLOYMENT,
            "annotations": {
                "energy-market/operation": operation,
                "energy-market/node-uid": node_uid,
            },
        },
        "spec": {
            "replicas": 1,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": label},
            "template": {
                "metadata": {"labels": label},
                "spec": {
                    "automountServiceAccountToken": False,
                    "nodeSelector": {"kubernetes.io/hostname": NODE},
                    "containers": [container],
                    "volumes": volumes,
                },
            },
        },
    }
    service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": DEPLOYMENT},
        "spec": {
            "type": "ClusterIP",
            "selector": label,
            "ports": [{"name": "http", "port": 8766, "targetPort": 8766}],
        },
    }
    policy = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": POLICY},
        "spec": {
            "podSelector": {"matchLabels": label},
            "policyTypes": ["Ingress"],
            "ingress": [
                {
                    "from": [
                        {
                            "podSelector": {
                                "matchLabels": {
                                    "app.kubernetes.io/component": "sandbox"
                                }
                            }
                        },
                        {
                            "podSelector": {
                                "matchLabels": {
                                    "energy-market/maintenance": maintenance_operation
                                    or operation
                                }
                            }
                        },
                    ],
                    "ports": [{"protocol": "TCP", "port": 8766}],
                }
            ],
        },
    }
    return [policy, service, deployment]


STAGE = "import json\ntry:\n\n import hashlib,importlib.util,io,json,os,pathlib,shutil,stat,subprocess,sys,zipfile\n operation,expected=sys.argv[1:]\n root=pathlib.Path('/mnt/skills'); public=root/'public'\n if any(p.is_symlink() for p in (root,public)):raise ValueError('ROOT_SYMLINK')\n if (public/'energy-market-daily').exists() or (public/'energy-market-data').exists():raise ValueError('TARGET_EXISTS')\n release=root/('.energy-market-release-'+operation)\n release.mkdir(mode=0o700)\n raw=sys.stdin.buffer.read(6*1024*1024+1)\n if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('ARCHIVE_HASH')\n with zipfile.ZipFile(io.BytesIO(raw)) as z:\n  seen=set(); total=0\n  for item in z.infolist():\n   p=pathlib.PurePosixPath(item.filename)\n   if item.is_dir():continue\n   if p.is_absolute() or '..' in p.parts or '\\\\' in item.filename or ':' in item.filename or p.parts[0]!='energy-market-daily' or stat.S_ISLNK(item.external_attr>>16) or item.filename in seen:raise ValueError('ARCHIVE_PATH')\n   seen.add(item.filename);total+=item.file_size\n   if total>12*1024*1024 or len(seen)>200:raise ValueError('ARCHIVE_BOUND')\n   dest=release.joinpath(*p.parts);dest.parent.mkdir(parents=True,exist_ok=True)\n   with dest.open('xb') as f:f.write(z.read(item))\n skill=release/'energy-market-daily'\n # ENTRY_OUTER_PATCH: only the external launcher changes, never upstream/.\n import base64\n outer=skill/'scripts/entry.py'\n if hashlib.sha256(outer.read_bytes()).hexdigest()!='5582901fc9268919e86dfb474b3561c39b951d18832ffb17c8695fd5e8f19a2c':raise ValueError('OUTER_BEFORE_HASH')\n replacement=base64.b64decode('IiIiVGhpbiBsYXVuY2hlciBmb3IgdGhlIGJ5dGUtaWRlbnRpY2FsIHVwc3RyZWFtIHByb2plY3Q7IG5vIGJ1c2luZXNzIGNhbGN1bGF0aW9ucy4iIiIKCmltcG9ydCBhcmdwYXJzZQppbXBvcnQgZGF0ZXRpbWUgYXMgZHQKaW1wb3J0IGhhc2hsaWIKaW1wb3J0IGh0dHAuY2xpZW50CmltcG9ydCBpbwppbXBvcnQganNvbgppbXBvcnQgb3MKZnJvbSBwYXRobGliIGltcG9ydCBQYXRoLCBQdXJlUG9zaXhQYXRoCmltcG9ydCBzaHV0aWwKaW1wb3J0IHNpZ25hbAppbXBvcnQgc3RhdAppbXBvcnQgc3VicHJvY2VzcwppbXBvcnQgc3lzCmltcG9ydCB0ZW1wZmlsZQppbXBvcnQgdGltZQppbXBvcnQgdXVpZAppbXBvcnQgemlwZmlsZQoKUk9PVCA9IFBhdGgoX19maWxlX18pLnJlc29sdmUoKS5wYXJlbnRzWzFdCkFSQ0hJVkVfU0hBID0gImViNjY4NDNiYmU2MTcwZDg2ZDMzYjY1ZWZhNTkzYmUyNjNlNzc4Mjc1YzZmN2Q0OTFlMWZkMGMzYzZkOThmOTkiClRaID0gZHQudGltZXpvbmUoZHQudGltZWRlbHRhKGhvdXJzPTgpKQpNVVRBQkxFID0gKCJhcHAvZGF0YS8iLCAiYXBwL3NvdXJjZXMvIiwgImFwcC9vdXRwdXQvIiwgImFwcC9zdGF0ZS8iLCAicmVwcm9kdWNlZC8iKQpDT0xMRUNUT1JfSE9TVCA9ICJlbmVyZ3ktbWFya2V0LWNvbGxlY3Rvci5kZWVyLWZsb3cuc3ZjLmNsdXN0ZXIubG9jYWwiCiMgUXVhbGlmaWVkIG9uIHRoZSBleGlzdGluZyBBUk02NCBkb2N1bWVudC1ydW50aW1lMSBpbWFnZS4gRnVsbCBDaHJvbWUncyBHUFUKIyBwcm9jZXNzIGNyYXNoZXMgb24gdGhhdCBydW50aW1lOyB0aGlzIGJ1bmRsZWQgaGVhZGxlc3MgZXhlY3V0YWJsZSBwYXNzZWQgdGhlCiMgc2FtZSBsYXVuY2gvbmV3LXBhZ2Uvc2NyZWVuc2hvdCBwYXRoLiBObyBicm93c2VyIGRvd25sb2FkIG9yIHVwc3RyZWFtIGVkaXQuCkxJTlVYX1JFUE9SVF9CUk9XU0VSID0gIi9yb290Ly5jYWNoZS9tcy1wbGF5d3JpZ2h0L2Nocm9taXVtX2hlYWRsZXNzX3NoZWxsLTEyMjgvY2hyb21lLWxpbnV4L2hlYWRsZXNzX3NoZWxsIgoKCmRlZiByZXF1aXJlKG9rLCBjb2RlKToKICAgIGlmIG5vdCBvazoKICAgICAgICByYWlzZSBWYWx1ZUVycm9yKGNvZGUpCgoKZGVmIHNoYShyYXcpOgogICAgcmV0dXJuIGhhc2hsaWIuc2hhMjU2KHJhdykuaGV4ZGlnZXN0KCkKCgpkZWYgcmVhZChwYXRoLCBsaW1pdD01ICogMTAyNCAqIDEwMjQpOgogICAgcGF0aCA9IFBhdGgocGF0aCkKICAgIHJlcXVpcmUobm90IGFueShwLmlzX3N5bWxpbmsoKSBmb3IgcCBpbiBbcGF0aCwgKnBhdGgucGFyZW50c10pLCAiU1lNTElOS19SRUZVU0VEIikKICAgIGZkID0gb3Mub3BlbigKICAgICAgICBwYXRoLCBvcy5PX1JET05MWSB8IGdldGF0dHIob3MsICJPX05PRk9MTE9XIiwgMCkgfCBnZXRhdHRyKG9zLCAiT19OT05CTE9DSyIsIDApCiAgICApCiAgICB0cnk6CiAgICAgICAgYmVmb3JlID0gb3MuZnN0YXQoZmQpCiAgICAgICAgcmVxdWlyZSgKICAgICAgICAgICAgc3RhdC5TX0lTUkVHKGJlZm9yZS5zdF9tb2RlKSBhbmQgYmVmb3JlLnN0X3NpemUgPD0gbGltaXQsCiAgICAgICAgICAgICJGSUxFX0JPVU5EX09SX1RZUEUiLAogICAgICAgICkKICAgICAgICB3aXRoIG9zLmZkb3BlbihmZCwgInJiIiwgY2xvc2VmZD1GYWxzZSkgYXMgZjoKICAgICAgICAgICAgcmF3ID0gZi5yZWFkKGxpbWl0ICsgMSkKICAgICAgICBhZnRlciA9IG9zLmZzdGF0KGZkKQogICAgICAgIHJlcXVpcmUoCiAgICAgICAgICAgIGxlbihyYXcpIDw9IGxpbWl0CiAgICAgICAgICAgIGFuZCAoYmVmb3JlLnN0X2lubywgYmVmb3JlLnN0X3NpemUsIGJlZm9yZS5zdF9tdGltZV9ucykKICAgICAgICAgICAgPT0gKGFmdGVyLnN0X2lubywgYWZ0ZXIuc3Rfc2l6ZSwgYWZ0ZXIuc3RfbXRpbWVfbnMpLAogICAgICAgICAgICAiRklMRV9DSEFOR0VEX0RVUklOR19SRUFEIiwKICAgICAgICApCiAgICAgICAgcmV0dXJuIHJhdwogICAgZmluYWxseToKICAgICAgICBvcy5jbG9zZShmZCkKCgpkZWYgc2F2ZShwYXRoLCBkYXRhKToKICAgIHBhdGggPSBQYXRoKHBhdGgpCiAgICByZXF1aXJlKG5vdCBhbnkocC5pc19zeW1saW5rKCkgZm9yIHAgaW4gW3BhdGgsICpwYXRoLnBhcmVudHNdKSwgIlNZTUxJTktfUkVGVVNFRCIpCiAgICB0bXAgPSBwYXRoLndpdGhfbmFtZSgiLiIgKyBwYXRoLm5hbWUgKyAiLiIgKyB1dWlkLnV1aWQ0KCkuaGV4KQogICAgd2l0aCB0bXAub3BlbigieCIsIGVuY29kaW5nPSJ1dGYtOCIpIGFzIGY6CiAgICAgICAganNvbi5kdW1wKAogICAgICAgICAgICBkYXRhLCBmLCBlbnN1cmVfYXNjaWk9RmFsc2UsIHNvcnRfa2V5cz1UcnVlLCBpbmRlbnQ9MiwgYWxsb3dfbmFuPUZhbHNlCiAgICAgICAgKQogICAgb3MucmVwbGFjZSh0bXAsIHBhdGgpCgoKZGVmIG9yaWdpbmFsX2ZpbGVzKCk6CiAgICByYXcgPSByZWFkKFJPT1QgLyAiYXNzZXRzL2VuZXJneS1kYWlseS12MS4wLm9yaWdpbmFsLnppcCIpCiAgICByZXF1aXJlKHNoYShyYXcpID09IEFSQ0hJVkVfU0hBLCAiT1JJR0lOQUxfQVJDSElWRV9DSEFOR0VEIikKICAgIGV4cGVjdGVkID0ge30KICAgIHdpdGggemlwZmlsZS5aaXBGaWxlKGlvLkJ5dGVzSU8ocmF3KSkgYXMgYXJjaGl2ZToKICAgICAgICBmb3IgaXRlbSBpbiBhcmNoaXZlLmluZm9saXN0KCk6CiAgICAgICAgICAgIHBhdGggPSBQdXJlUG9zaXhQYXRoKGl0ZW0uZmlsZW5hbWUpCiAgICAgICAgICAgIHJlcXVpcmUoCiAgICAgICAgICAgICAgICBub3QgcGF0aC5pc19hYnNvbHV0ZSgpCiAgICAgICAgICAgICAgICBhbmQgIi4uIiBub3QgaW4gcGF0aC5wYXJ0cwogICAgICAgICAgICAgICAgYW5kICI6IiBub3QgaW4gaXRlbS5maWxlbmFtZQogICAgICAgICAgICAgICAgYW5kICJcXCIgbm90IGluIGl0ZW0uZmlsZW5hbWUKICAgICAgICAgICAgICAgIGFuZCBub3Qgc3RhdC5TX0lTTE5LKGl0ZW0uZXh0ZXJuYWxfYXR0ciA+PiAxNiksCiAgICAgICAgICAgICAgICAiQVJDSElWRV9QQVRIIiwKICAgICAgICAgICAgKQogICAgICAgICAgICBpZiBpdGVtLmlzX2RpcigpOgogICAgICAgICAgICAgICAgY29udGludWUKICAgICAgICAgICAgcGFydHMgPSAoCiAgICAgICAgICAgICAgICBwYXRoLnBhcnRzWzE6XSBpZiBwYXRoLnBhcnRzWzBdID09ICJlbmVyZ3ktZGFpbHktdjEuMCIgZWxzZSBwYXRoLnBhcnRzCiAgICAgICAgICAgICkKICAgICAgICAgICAgbmFtZSA9IFB1cmVQb3NpeFBhdGgoKnBhcnRzKS5hc19wb3NpeCgpCiAgICAgICAgICAgIHJlcXVpcmUobmFtZSBub3QgaW4gZXhwZWN0ZWQsICJBUkNISVZFX0RVUExJQ0FURSIpCiAgICAgICAgICAgIGV4cGVjdGVkW25hbWVdID0gc2hhKGFyY2hpdmUucmVhZChpdGVtKSkKICAgIHJlcXVpcmUobGVuKGV4cGVjdGVkKSA9PSAxNTgsICJPUklHSU5BTF9GSUxFX1NFVF9DSEFOR0VEIikKICAgIHZlcmlmeV90cmVlKFJPT1QgLyAidXBzdHJlYW0iLCBleHBlY3RlZCwgRmFsc2UpCiAgICByZXR1cm4gZXhwZWN0ZWQKCgpkZWYgdmVyaWZ5X3RyZWUoZm9sZGVyLCBleHBlY3RlZCwgd29ya2luZyk6CiAgICBhY3R1YWwgPSBzZXQoKQogICAgZm9yIHAgaW4gZm9sZGVyLnJnbG9iKCIqIik6CiAgICAgICAgcmVxdWlyZShub3QgcC5pc19zeW1saW5rKCksICJTWU1MSU5LX1JFRlVTRUQiKQogICAgICAgIGlmIHAuaXNfZmlsZSgpOgogICAgICAgICAgICByZWwgPSBwLnJlbGF0aXZlX3RvKGZvbGRlcikuYXNfcG9zaXgoKQogICAgICAgICAgICBpZiB3b3JraW5nIGFuZCByZWwuc3RhcnRzd2l0aChNVVRBQkxFKToKICAgICAgICAgICAgICAgIGNvbnRpbnVlCiAgICAgICAgICAgIHJlcXVpcmUoCiAgICAgICAgICAgICAgICByZWwgaW4gZXhwZWN0ZWQgYW5kIHNoYShyZWFkKHApKSA9PSBleHBlY3RlZFtyZWxdLAogICAgICAgICAgICAgICAgIk9SSUdJTkFMX0ZJTEVfQ0hBTkdFRCIsCiAgICAgICAgICAgICkKICAgICAgICAgICAgYWN0dWFsLmFkZChyZWwpCiAgICBuZWVkZWQgPSB7biBmb3IgbiBpbiBleHBlY3RlZCBpZiBub3QgKHdvcmtpbmcgYW5kIG4uc3RhcnRzd2l0aChNVVRBQkxFKSl9CiAgICByZXF1aXJlKGFjdHVhbCA9PSBuZWVkZWQsICJPUklHSU5BTF9GSUxFX01JU1NJTkciKQoKCmRlZiBpbnB1dF9oYXNoZXMoZW5naW5lKToKICAgIHJlc3VsdCwgdG90YWwgPSB7fSwgMAogICAgZm9yIHN1YiBpbiAoImFwcC9kYXRhIiwgImFwcC9zb3VyY2VzIik6CiAgICAgICAgZm9yIHAgaW4gc29ydGVkKChlbmdpbmUgLyBzdWIpLnJnbG9iKCIqIikpOgogICAgICAgICAgICByZXF1aXJlKG5vdCBwLmlzX3N5bWxpbmsoKSwgIklOUFVUX1NZTUxJTksiKQogICAgICAgICAgICBpZiBwLmlzX2ZpbGUoKToKICAgICAgICAgICAgICAgIGRhdGEgPSByZWFkKHAsIDQgKiAxMDI0ICogMTAyNCkKICAgICAgICAgICAgICAgIHRvdGFsICs9IGxlbihkYXRhKQogICAgICAgICAgICAgICAgcmVxdWlyZSh0b3RhbCA8PSAzMiAqIDEwMjQgKiAxMDI0IGFuZCBsZW4ocmVzdWx0KSA8IDQwMCwgIklOUFVUX0JVREdFVCIpCiAgICAgICAgICAgICAgICByZXN1bHRbcC5yZWxhdGl2ZV90byhlbmdpbmUpLmFzX3Bvc2l4KCldID0gc2hhKGRhdGEpCiAgICByZXR1cm4gcmVzdWx0CgoKZGVmIG9yaWdpbmFsX2NvbW1hbmQocHJvamVjdCwgc2NyaXB0LCBhcmdzLCBicm93c2VyPU5vbmUsIHRpbWVvdXQ9MTIwKToKICAgICMgVXNlIGEgcHJpdmF0ZSBzdWJwcm9jZXNzLCBvcmlnaW5hbCBhcmd2IGFuZCB0aGUgZXhpc3RpbmcgdXBzdHJlYW0gYnJvd3NlciBvdmVycmlkZS4KICAgIGVudl9uYW1lcyA9ICgKICAgICAgICAiUEFUSCIsCiAgICAgICAgIlN5c3RlbVJvb3QiLAogICAgICAgICJXSU5ESVIiLAogICAgICAgICJIT01FIiwKICAgICAgICAiVVNFUlBST0ZJTEUiLAogICAgICAgICJMT0NBTEFQUERBVEEiLAogICAgICAgICJURU1QIiwKICAgICAgICAiVE1QIiwKICAgICAgICAiVE1QRElSIiwKICAgICAgICAiTERfTElCUkFSWV9QQVRIIiwKICAgICAgICAiTEFORyIsCiAgICAgICAgIkxDX0FMTCIsCiAgICAgICAgIlBMQVlXUklHSFRfQlJPV1NFUlNfUEFUSCIsCiAgICAgICAgIkZPTlRDT05GSUdfUEFUSCIsCiAgICApCiAgICBlbnYgPSB7azogb3MuZW52aXJvbltrXSBmb3IgayBpbiBlbnZfbmFtZXMgaWYgayBpbiBvcy5lbnZpcm9ufQogICAgZW52LnVwZGF0ZShQWVRIT05VVEY4PSIxIiwgUFlUSE9ORE9OVFdSSVRFQllURUNPREU9IjEiLCBUWj0iQXNpYS9TaGFuZ2hhaSIpCiAgICBpZiBicm93c2VyOgogICAgICAgIHJlcXVpcmUoUGF0aChicm93c2VyKS5pc19maWxlKCksICJCUk9XU0VSX0VYRUNVVEFCTEVfTUlTU0lORyIpCiAgICAgICAgZW52WyJSRVBPUlRfQlJPV1NFUl9FWEVDVVRBQkxFIl0gPSBzdHIoYnJvd3NlcikKICAgIGVuZ2luZSA9IHByb2plY3QgLyAiZW5naW5lIgogICAgY29tbWFuZCA9IFtzeXMuZXhlY3V0YWJsZSwgIi1CIiwgc3RyKGVuZ2luZSAvICJhcHAiIC8gc2NyaXB0KV0gKyBhcmdzCiAgICB3aXRoIHRlbXBmaWxlLlRlbXBvcmFyeUZpbGUoKSBhcyBsb2c6CiAgICAgICAgY2hpbGQgPSBzdWJwcm9jZXNzLlBvcGVuKAogICAgICAgICAgICBjb21tYW5kLAogICAgICAgICAgICBjd2Q9c3RyKGVuZ2luZSksCiAgICAgICAgICAgIGVudj1lbnYsCiAgICAgICAgICAgIHN0ZG91dD1sb2csCiAgICAgICAgICAgIHN0ZGVycj1sb2csCiAgICAgICAgICAgIHN0YXJ0X25ld19zZXNzaW9uPW9zLm5hbWUgIT0gIm50IiwKICAgICAgICApCiAgICAgICAgdHJ5OgogICAgICAgICAgICBjaGlsZC53YWl0KHRpbWVvdXQ9dGltZW91dCkKICAgICAgICBleGNlcHQgc3VicHJvY2Vzcy5UaW1lb3V0RXhwaXJlZDoKICAgICAgICAgICAgaWYgb3MubmFtZSAhPSAibnQiOgogICAgICAgICAgICAgICAgb3Mua2lsbHBnKGNoaWxkLnBpZCwgc2lnbmFsLlNJR0tJTEwpCiAgICAgICAgICAgIGVsc2U6CiAgICAgICAgICAgICAgICBjaGlsZC5raWxsKCkKICAgICAgICAgICAgY2hpbGQud2FpdCh0aW1lb3V0PTEwKQogICAgICAgICAgICByYWlzZSBWYWx1ZUVycm9yKCJPUklHSU5BTF9DT01NQU5EX1RJTUVPVVQiKSBmcm9tIE5vbmUKICAgICAgICBzaXplID0gbG9nLnRlbGwoKQogICAgICAgIHJlcXVpcmUoc2l6ZSA8PSAyNTYgKiAxMDI0LCAiT1JJR0lOQUxfT1VUUFVUX0JPVU5EIikKICAgICAgICBsb2cuc2VlaygwKQogICAgICAgIG91dHB1dCA9IGxvZy5yZWFkKCkKICAgIHJlcXVpcmUoCiAgICAgICAgY2hpbGQucmV0dXJuY29kZSA9PSAwLAogICAgICAgICJPUklHSU5BTF9DT01NQU5EX0ZBSUxFRF8iICsgc2NyaXB0LnJlcGxhY2UoIi5weSIsICIiKS51cHBlcigpLAogICAgKQogICAgcmV0dXJuIG91dHB1dAoKCmRlZiBwcm9qZWN0X21ldGEocHJvamVjdCwgZXhwZWN0ZWQpOgogICAgbWV0YSA9IGpzb24ubG9hZHMocmVhZChwcm9qZWN0IC8gIlBST0pFQ1QuanNvbiIpKQogICAgcmVxdWlyZSgKICAgICAgICBtZXRhLmdldCgic2NoZW1hIikgPT0gImVuZXJneS1kYWlseS1zZXNzaW9uLnYxIgogICAgICAgIGFuZCBtZXRhLmdldCgiYXJjaGl2ZV9zaGEyNTYiKSA9PSBBUkNISVZFX1NIQSwKICAgICAgICAiUFJPSkVDVF9JREVOVElUWSIsCiAgICApCiAgICBkYXkgPSBkdC5kYXRlLmZyb21pc29mb3JtYXQobWV0YVsicHVibGljYXRpb25fZGF0ZSJdKQogICAgcmVxdWlyZSgKICAgICAgICBkYXkuaXNvZm9ybWF0KCkgPT0gbWV0YVsicHVibGljYXRpb25fZGF0ZSJdCiAgICAgICAgYW5kIGRheSA8PSBkdC5kYXRldGltZS5ub3coVFopLmRhdGUoKSwKICAgICAgICAiUFVCTElDQVRJT05fREFURSIsCiAgICApCiAgICB2ZXJpZnlfdHJlZShwcm9qZWN0IC8gImVuZ2luZSIsIGV4cGVjdGVkLCBUcnVlKQogICAgcmV0dXJuIG1ldGEKCgpkZWYgaW5pdChwcm9qZWN0LCBkYXksIGV4cGVjdGVkKToKICAgIGRheSA9IGRheSBvciBkdC5kYXRldGltZS5ub3coVFopLmRhdGUoKS5pc29mb3JtYXQoKQogICAgcHVibGljYXRpb24gPSBkdC5kYXRlLmZyb21pc29mb3JtYXQoZGF5KQogICAgcmVxdWlyZSgKICAgICAgICBkYXkgPT0gcHVibGljYXRpb24uaXNvZm9ybWF0KCkgYW5kIHB1YmxpY2F0aW9uIDw9IGR0LmRhdGV0aW1lLm5vdyhUWikuZGF0ZSgpLAogICAgICAgICJQVUJMSUNBVElPTl9EQVRFIiwKICAgICkKICAgIHJlcXVpcmUobm90IHByb2plY3QuZXhpc3RzKCksICJQUk9KRUNUX0VYSVNUUyIpCiAgICBwcm9qZWN0Lm1rZGlyKHBhcmVudHM9VHJ1ZSkKICAgIHNodXRpbC5jb3B5dHJlZShST09UIC8gInVwc3RyZWFtIiwgcHJvamVjdCAvICJlbmdpbmUiKQogICAgdmVyaWZ5X3RyZWUocHJvamVjdCAvICJlbmdpbmUiLCBleHBlY3RlZCwgRmFsc2UpCiAgICBtZXRhID0gZGljdCgKICAgICAgICBzY2hlbWE9ImVuZXJneS1kYWlseS1zZXNzaW9uLnYxIiwKICAgICAgICBwdWJsaWNhdGlvbl9kYXRlPWRheSwKICAgICAgICBhcmNoaXZlX3NoYTI1Nj1BUkNISVZFX1NIQSwKICAgICAgICBjcmVhdGVkX2F0PWR0LmRhdGV0aW1lLm5vdyhUWikuaXNvZm9ybWF0KCksCiAgICAgICAgYnVzaW5lc3NfY29kZV9tb2RpZmllZD1GYWxzZSwKICAgICkKICAgIHNhdmUocHJvamVjdCAvICJQUk9KRUNULmpzb24iLCBtZXRhKQogICAgcmV0dXJuIGRpY3QoCiAgICAgICAgc3RhdHVzPSJPUklHSU5BTF9DT1BJRURfSU5QVVRTX1JFUVVJUkVEIiwKICAgICAgICBvcmlnaW5hbF9maWxlcz1sZW4oZXhwZWN0ZWQpLAogICAgICAgIGVuZ2luZT1zdHIocHJvamVjdCAvICJlbmdpbmUiKSwKICAgICAgICBwdWJsaWNhdGlvbl9kYXRlPWRheSwKICAgICkKCgpkZWYgaW1wb3J0X21hcmtldChwcm9qZWN0LCBjYWNoZSwgbWV0YSk6CiAgICAjIE1hdGNoIHRoZSB1cHN0cmVhbSBmaWxlbmFtZSBjb250cmFjdC4gQ29weSBvbmx5IGZpeGVkIG1hcmtldCByZXNwb25zZXMsIG5ldmVyIGNyZWRlbnRpYWxzLgogICAgZnJvbSBjYWxlbmRhciBpbXBvcnQgbW9udGhyYW5nZQoKICAgIHRhcmdldCA9IGR0LmRhdGUuZnJvbWlzb2Zvcm1hdChtZXRhWyJwdWJsaWNhdGlvbl9kYXRlIl0pIC0gZHQudGltZWRlbHRhKGRheXM9MSkKICAgIHllYXIsIG1vbnRoID0gZGl2bW9kKHRhcmdldC55ZWFyICogMTIgKyB0YXJnZXQubW9udGggLSA0LCAxMikKICAgIGZpcnN0ID0gZHQuZGF0ZSh5ZWFyLCBtb250aCArIDEsIG1pbih0YXJnZXQuZGF5LCBtb250aHJhbmdlKHllYXIsIG1vbnRoICsgMSlbMV0pKQogICAgY29waWVzID0ge30KICAgIGZvciBuIGluIHJhbmdlKCh0YXJnZXQgLSBmaXJzdCkuZGF5cyArIDEpOgogICAgICAgIGRheSA9IChmaXJzdCArIGR0LnRpbWVkZWx0YShkYXlzPW4pKS5pc29mb3JtYXQoKQogICAgICAgIG5hbWUgPSAiamt4X25ld18iICsgZGF5ICsgIi5qc29uIgogICAgICAgIHBheWxvYWQgPSByZWFkKFBhdGgoY2FjaGUpIC8gbmFtZSwgMjU2ICogMTAyNCkKICAgICAgICBvYmogPSBqc29uLmxvYWRzKHBheWxvYWQpCiAgICAgICAgcmVxdWlyZSgKICAgICAgICAgICAgaXNpbnN0YW5jZShvYmosIGRpY3QpCiAgICAgICAgICAgIGFuZCBzdHIob2JqLmdldCgiZXJyY29kZSIpKSA9PSAiMCIKICAgICAgICAgICAgYW5kIGlzaW5zdGFuY2Uob2JqLmdldCgiZGF0YSIpLCBsaXN0KQogICAgICAgICAgICBhbmQgbGVuKG9ialsiZGF0YSJdKSA8PSA1MTIsCiAgICAgICAgICAgICJNQVJLRVRfQ0FDSEVfU0hBUEUiLAogICAgICAgICkKICAgICAgICBjb3BpZXNbbmFtZV0gPSBwYXlsb2FkCiAgICBmb3IgbmFtZSwgcGF5bG9hZCBpbiBjb3BpZXMuaXRlbXMoKToKICAgICAgICBkZXN0aW5hdGlvbiA9IHByb2plY3QgLyAiZW5naW5lL2FwcC9zb3VyY2VzIiAvIG5hbWUKICAgICAgICByZXF1aXJlKG5vdCBkZXN0aW5hdGlvbi5pc19zeW1saW5rKCksICJJTlBVVF9TWU1MSU5LIikKICAgICAgICBkZXN0aW5hdGlvbi53cml0ZV9ieXRlcyhwYXlsb2FkKQogICAgIyBXaXRoIHRoZSBmdWxsIGNhY2hlIHByZXNlbnQsIHRoZSB1bmNoYW5nZWQgcHJvZ3JhbSB0YWtlcyBpdHMgbG9jYWwtY2FjaGUgYnJhbmNoLgogICAgb3JpZ2luYWxfY29tbWFuZCgKICAgICAgICBwcm9qZWN0LCAiZmV0Y2hfbWFya2V0X2hpc3RvcnkucHkiLCBbIi0tZGF0ZSIsIHRhcmdldC5pc29mb3JtYXQoKV0sIHRpbWVvdXQ9MTIwCiAgICApCiAgICByZXR1cm4gZGljdChzdGF0dXM9Ik9SSUdJTkFMX01BUktFVF9TQ1JJUFRfQ09NUExFVEVEIiwgY2FjaGVfZGF5cz1sZW4oY29waWVzKSkKCgpkZWYgcmVxdWVzdF9tYXJrZXQoZGF5KToKICAgICMgQSBmaXhlZCBpbnRlcm5hbCBhZGRyZXNzLCBubyBwcm94eSwgcmVkaXJlY3QsIGNyZWRlbnRpYWwgb3IgYXJiaXRyYXJ5IFVSTC4KICAgIGNvbm5lY3Rpb24gPSBodHRwLmNsaWVudC5IVFRQQ29ubmVjdGlvbihDT0xMRUNUT1JfSE9TVCwgODc2NiwgdGltZW91dD0xMCkKICAgIHRyeToKICAgICAgICBjb25uZWN0aW9uLnJlcXVlc3QoCiAgICAgICAgICAgICJQT1NUIiwKICAgICAgICAgICAgIi9wcmVwYXJlLyIgKyBkYXksCiAgICAgICAgICAgIGJvZHk9YiIiLAogICAgICAgICAgICBoZWFkZXJzPXsiQWNjZXB0IjogImFwcGxpY2F0aW9uL2pzb24iLCAiQ29ubmVjdGlvbiI6ICJjbG9zZSJ9LAogICAgICAgICkKICAgICAgICByZXNwb25zZSA9IGNvbm5lY3Rpb24uZ2V0cmVzcG9uc2UoKQogICAgICAgIHJhdyA9IHJlc3BvbnNlLnJlYWQoNDA5NykKICAgICAgICByZXF1aXJlKAogICAgICAgICAgICByZXNwb25zZS5zdGF0dXMgaW4gKDIwMCwgMjAyKSBhbmQgbGVuKHJhdykgPD0gNDA5NiwKICAgICAgICAgICAgIkNPTExFQ1RPUl9SRVNQT05TRV9SRUpFQ1RFRCIsCiAgICAgICAgKQogICAgICAgIGRhdGEgPSBqc29uLmxvYWRzKHJhdykKICAgICAgICByZXF1aXJlKAogICAgICAgICAgICBpc2luc3RhbmNlKGRhdGEsIGRpY3QpIGFuZCBkYXRhLmdldCgiZGF0YV9kYXRlIikgPT0gZGF5LAogICAgICAgICAgICAiQ09MTEVDVE9SX0RBVEVfTUlTTUFUQ0giLAogICAgICAgICkKICAgICAgICByZXF1aXJlKAogICAgICAgICAgICBkYXRhLmdldCgic3RhdHVzIikgaW4gKCJSRUFEWSIsICJDT0xMRUNUSU5HIiwgIldBSVRfREFUQSIsICJJTkNPTVBMRVRFIiksCiAgICAgICAgICAgICJDT0xMRUNUT1JfU1RBVFVTX1JFSkVDVEVEIiwKICAgICAgICApCiAgICAgICAgcmV0dXJuIGRhdGEKICAgIGZpbmFsbHk6CiAgICAgICAgY29ubmVjdGlvbi5jbG9zZSgpCgoKZGVmIHJlZnJlc2hfbWFya2V0KHByb2plY3QsIG1ldGEpOgogICAgdGFyZ2V0ID0gKAogICAgICAgIGR0LmRhdGUuZnJvbWlzb2Zvcm1hdChtZXRhWyJwdWJsaWNhdGlvbl9kYXRlIl0pIC0gZHQudGltZWRlbHRhKGRheXM9MSkKICAgICkuaXNvZm9ybWF0KCkKICAgIGRlYWRsaW5lID0gdGltZS5tb25vdG9uaWMoKSArIDYyMAogICAgd2hpbGUgVHJ1ZToKICAgICAgICBvYnNlcnZlZCA9IHJlcXVlc3RfbWFya2V0KHRhcmdldCkKICAgICAgICBpZiBvYnNlcnZlZFsic3RhdHVzIl0gPT0gIlJFQURZIjoKICAgICAgICAgICAgcmVzdWx0ID0gaW1wb3J0X21hcmtldChwcm9qZWN0LCBST09ULnBhcmVudCAvICJlbmVyZ3ktbWFya2V0LWRhdGEiLCBtZXRhKQogICAgICAgICAgICByZXR1cm4gZGljdCgKICAgICAgICAgICAgICAgIHJlc3VsdCwKICAgICAgICAgICAgICAgIGRhdGFfZGF0ZT10YXJnZXQsCiAgICAgICAgICAgICAgICBjcmVkZW50aWFsX2luX3NhbmRib3g9RmFsc2UsCiAgICAgICAgICAgICAgICBjb2xsZWN0b3JfY29ubmVjdGlvbnM9b2JzZXJ2ZWQuZ2V0KCJjb25uZWN0aW9ucyIpLAogICAgICAgICAgICApCiAgICAgICAgaWYgb2JzZXJ2ZWRbInN0YXR1cyJdID09ICJXQUlUX0RBVEEiOgogICAgICAgICAgICByZXR1cm4gZGljdCgKICAgICAgICAgICAgICAgIHN0YXR1cz0iV0FJVF9QUk9WSURFUl9EQVRBIiwKICAgICAgICAgICAgICAgIGRhdGFfZGF0ZT10YXJnZXQsCiAgICAgICAgICAgICAgICBjcmVkZW50aWFsX2luX3NhbmRib3g9RmFsc2UsCiAgICAgICAgICAgICAgICByZXRyeV9hZnRlcl9zZWNvbmRzPTMwMCwKICAgICAgICAgICAgKQogICAgICAgIHJlcXVpcmUob2JzZXJ2ZWRbInN0YXR1cyJdICE9ICJJTkNPTVBMRVRFIiwgIkNPTExFQ1RPUl9GQUlMRUQiKQogICAgICAgIHJlcXVpcmUodGltZS5tb25vdG9uaWMoKSA8IGRlYWRsaW5lLCAiQ09MTEVDVE9SX1dBSVRfVElNRU9VVCIpCiAgICAgICAgdGltZS5zbGVlcCg1KQoKCmRlZiByZXBvcnRfYnJvd3NlcihvdmVycmlkZT1Ob25lKToKICAgIGlmIG92ZXJyaWRlOgogICAgICAgIGNhbmRpZGF0ZSA9IG92ZXJyaWRlCiAgICBlbGlmIHN5cy5wbGF0Zm9ybS5zdGFydHN3aXRoKCJsaW51eCIpOgogICAgICAgIGNhbmRpZGF0ZSA9IExJTlVYX1JFUE9SVF9CUk9XU0VSCiAgICBlbHNlOgogICAgICAgIGNhbmRpZGF0ZSA9IHNodXRpbC53aGljaCgiY2hyb21pdW0iKSBvciBzaHV0aWwud2hpY2goImNocm9taXVtLWJyb3dzZXIiKQogICAgcmVxdWlyZShib29sKGNhbmRpZGF0ZSkgYW5kIFBhdGgoY2FuZGlkYXRlKS5pc19maWxlKCksICJBUFBST1ZFRF9CUk9XU0VSX1JFUVVJUkVEIikKICAgIHJldHVybiBzdHIoY2FuZGlkYXRlKQoKCmRlZiBidWlsZF9yZXBvcnQocHJvamVjdCwgbWV0YSwgZXhwZWN0ZWQsIGJyb3dzZXIpOgogICAgZGF5ID0gbWV0YVsicHVibGljYXRpb25fZGF0ZSJdCiAgICBvcmlnaW5hbF9jb21tYW5kKHByb2plY3QsICJ2YWxpZGF0ZV9pbnB1dHMucHkiLCBbIi0tZGF0ZSIsIGRheV0pCiAgICBpbnB1dHMgPSBpbnB1dF9oYXNoZXMocHJvamVjdCAvICJlbmdpbmUiKQogICAgYnJvd3NlciA9IHJlcG9ydF9icm93c2VyKGJyb3dzZXIpCiAgICBvcmlnaW5hbF9jb21tYW5kKAogICAgICAgIHByb2plY3QsCiAgICAgICAgImdlbmVyYXRlX3JlcG9ydC5weSIsCiAgICAgICAgWyItLWRhdGUiLCBkYXksICItLXBuZyJdLAogICAgICAgIGJyb3dzZXI9YnJvd3NlciwKICAgICAgICB0aW1lb3V0PTE4MCwKICAgICkKICAgIHZlcmlmeV90cmVlKHByb2plY3QgLyAiZW5naW5lIiwgZXhwZWN0ZWQsIFRydWUpCiAgICByZXF1aXJlKGlucHV0X2hhc2hlcyhwcm9qZWN0IC8gImVuZ2luZSIpID09IGlucHV0cywgIklOUFVUU19DSEFOR0VEX0RVUklOR19SRU5ERVIiKQogICAgc3RlbSA9ICLmsrnmsJTkuI7ph5Hono3luILlnLrml6XmiqVfIiArIGRheQogICAgYXJ0aWZhY3RzID0ge30KICAgIGZvciBzdWZmaXggaW4gKCIuaHRtbCIsICIucG5nIiwgIi5qc29uIik6CiAgICAgICAgbmFtZSA9IHN0ZW0gKyBzdWZmaXgKICAgICAgICByYXcgPSByZWFkKHByb2plY3QgLyAiZW5naW5lL2FwcC9vdXRwdXQiIC8gbmFtZSwgMTIgKiAxMDI0ICogMTAyNCkKICAgICAgICByZXF1aXJlKGxlbihyYXcpID4gMCwgIkVNUFRZX0FSVElGQUNUIikKICAgICAgICBhcnRpZmFjdHNbbmFtZV0gPSByYXcKICAgIHNuYXBzaG90ID0ganNvbi5sb2FkcyhhcnRpZmFjdHNbc3RlbSArICIuanNvbiJdKQogICAgcmVxdWlyZSgKICAgICAgICBzbmFwc2hvdFsicHVibGljYXRpb25fZGF0ZSJdID09IGRheQogICAgICAgIGFuZCBzbmFwc2hvdFsiZGF0YV9kYXRlIl0KICAgICAgICA9PSAoZHQuZGF0ZS5mcm9taXNvZm9ybWF0KGRheSkgLSBkdC50aW1lZGVsdGEoZGF5cz0xKSkuaXNvZm9ybWF0KCksCiAgICAgICAgIk9VVFBVVF9EQVRFIiwKICAgICkKICAgIGZyb20gUElMIGltcG9ydCBJbWFnZQoKICAgIHdpdGggSW1hZ2Uub3Blbihpby5CeXRlc0lPKGFydGlmYWN0c1tzdGVtICsgIi5wbmciXSkpIGFzIGltYWdlOgogICAgICAgIHJlcXVpcmUoCiAgICAgICAgICAgIGltYWdlLmZvcm1hdCA9PSAiUE5HIgogICAgICAgICAgICBhbmQgaW1hZ2Uud2lkdGggPT0gNzgwCiAgICAgICAgICAgIGFuZCA1MDAgPD0gaW1hZ2UuaGVpZ2h0IDw9IDM2MDAwLAogICAgICAgICAgICAiUE5HX1NIQVBFIiwKICAgICAgICApCiAgICAgICAgaW1hZ2UudmVyaWZ5KCkKICAgIGJ1aWxkX2lkID0gdXVpZC51dWlkNCgpLmhleAogICAgYWNjZXB0ZWQgPSBwcm9qZWN0IC8gImRlbGl2ZXJpZXMiIC8gYnVpbGRfaWQKICAgIGFjY2VwdGVkLm1rZGlyKHBhcmVudHM9VHJ1ZSkKICAgIGZvciBuYW1lLCByYXcgaW4gYXJ0aWZhY3RzLml0ZW1zKCk6CiAgICAgICAgKGFjY2VwdGVkIC8gbmFtZSkud3JpdGVfYnl0ZXMocmF3KQogICAgcmVjZWlwdCA9IGRpY3QoCiAgICAgICAgc2NoZW1hPSJlbmVyZ3ktZGFpbHktb3JpZ2luYWwtcnVuLnYxIiwKICAgICAgICBwdWJsaWNhdGlvbl9kYXRlPWRheSwKICAgICAgICBhcmNoaXZlX3NoYTI1Nj1BUkNISVZFX1NIQSwKICAgICAgICBpbnB1dF9zaGEyNTY9aW5wdXRzLAogICAgICAgIGFydGlmYWN0cz17bjogc2hhKGIpIGZvciBuLCBiIGluIGFydGlmYWN0cy5pdGVtcygpfSwKICAgICAgICBvcmlnaW5hbF9maWxlc192ZXJpZmllZD1sZW4oZXhwZWN0ZWQpLAogICAgICAgIGJ1c2luZXNzX2NvZGVfbW9kaWZpZWQ9RmFsc2UsCiAgICAgICAgdmlzdWFsX2FjY2VwdGFuY2VfcGVuZGluZz1UcnVlLAogICAgICAgIHByb3ZpZGVyX3JlcXVlc3RzPTAsCiAgICAgICAgd29ya19tZXNzYWdlc19zZW50PTAsCiAgICApCiAgICBzYXZlKGFjY2VwdGVkIC8gIlJFQ0VJUFQuanNvbiIsIHJlY2VpcHQpCiAgICBzYXZlKAogICAgICAgIHByb2plY3QgLyAiTEFURVNULmpzb24iLAogICAgICAgIGRpY3QoYnVpbGRfaWQ9YnVpbGRfaWQsIHJlY2VpcHRfc2hhMjU2PXNoYShyZWFkKGFjY2VwdGVkIC8gIlJFQ0VJUFQuanNvbiIpKSksCiAgICApCiAgICByZXR1cm4gaW5zcGVjdChwcm9qZWN0LCBleHBlY3RlZCkKCgpkZWYgaW5zcGVjdChwcm9qZWN0LCBleHBlY3RlZCk6CiAgICBtZXRhID0gcHJvamVjdF9tZXRhKHByb2plY3QsIGV4cGVjdGVkKQogICAgbGF0ZXN0ID0ganNvbi5sb2FkcyhyZWFkKHByb2plY3QgLyAiTEFURVNULmpzb24iKSkKICAgIHJ1biA9IGxhdGVzdFsiYnVpbGRfaWQiXQogICAgcmVxdWlyZShsZW4ocnVuKSA9PSAzMiBhbmQgYWxsKGMgaW4gIjAxMjM0NTY3ODlhYmNkZWYiIGZvciBjIGluIHJ1biksICJCVUlMRF9JRCIpCiAgICBvdXRwdXQgPSBwcm9qZWN0IC8gImRlbGl2ZXJpZXMiIC8gcnVuCiAgICByYXcgPSByZWFkKG91dHB1dCAvICJSRUNFSVBULmpzb24iKQogICAgcmVxdWlyZShzaGEocmF3KSA9PSBsYXRlc3RbInJlY2VpcHRfc2hhMjU2Il0sICJSRUNFSVBUX0NIQU5HRUQiKQogICAgcmVjZWlwdCA9IGpzb24ubG9hZHMocmF3KQogICAgcmVxdWlyZSgKICAgICAgICByZWNlaXB0WyJhcmNoaXZlX3NoYTI1NiJdID09IEFSQ0hJVkVfU0hBCiAgICAgICAgYW5kIHJlY2VpcHRbInB1YmxpY2F0aW9uX2RhdGUiXSA9PSBtZXRhWyJwdWJsaWNhdGlvbl9kYXRlIl0sCiAgICAgICAgIlJFQ0VJUFRfSURFTlRJVFkiLAogICAgKQogICAgcmVxdWlyZSgKICAgICAgICByZWNlaXB0WyJpbnB1dF9zaGEyNTYiXSA9PSBpbnB1dF9oYXNoZXMocHJvamVjdCAvICJlbmdpbmUiKSwKICAgICAgICAiSU5QVVRTX0NIQU5HRURfUkVCVUlMRCIsCiAgICApCiAgICBuYW1lcyA9IHsKICAgICAgICAi5rK55rCU5LiO6YeR6J6N5biC5Zy65pel5oqlXyIgKyBtZXRhWyJwdWJsaWNhdGlvbl9kYXRlIl0gKyBleHQKICAgICAgICBmb3IgZXh0IGluICgiLmh0bWwiLCAiLnBuZyIsICIuanNvbiIpCiAgICB9CiAgICByZXF1aXJlKHNldChyZWNlaXB0WyJhcnRpZmFjdHMiXSkgPT0gbmFtZXMsICJBUlRJRkFDVF9TRVQiKQogICAgZm9yIG5hbWUgaW4gbmFtZXM6CiAgICAgICAgcmVxdWlyZSgKICAgICAgICAgICAgc2hhKHJlYWQob3V0cHV0IC8gbmFtZSwgMTIgKiAxMDI0ICogMTAyNCkpID09IHJlY2VpcHRbImFydGlmYWN0cyJdW25hbWVdLAogICAgICAgICAgICAiQVJUSUZBQ1RfQ0hBTkdFRCIsCiAgICAgICAgKQogICAgYWNjZXB0ZWQgPSBvdXRwdXQgLyAiVklTVUFMLUFDQ0VQVEFOQ0UuanNvbiIKICAgIHJldmlld2VkID0gRmFsc2UKICAgIGlmIGFjY2VwdGVkLmV4aXN0cygpOgogICAgICAgIHJldmlldyA9IGpzb24ubG9hZHMocmVhZChhY2NlcHRlZCkpCiAgICAgICAgcmVxdWlyZSgKICAgICAgICAgICAgcmV2aWV3LmdldCgicmVjZWlwdF9zaGEyNTYiKSA9PSBzaGEocmF3KQogICAgICAgICAgICBhbmQgcmV2aWV3LmdldCgidmlzdWFsX3Jldmlld2VkIikgaXMgVHJ1ZSwKICAgICAgICAgICAgIlZJU1VBTF9SRUNFSVBUX0NIQU5HRUQiLAogICAgICAgICkKICAgICAgICByZXZpZXdlZCA9IFRydWUKICAgIHJldHVybiBkaWN0KAogICAgICAgIHN0YXR1cz0iT1JJR0lOQUxfUkVQT1JUX0FDQ0VQVEVEIgogICAgICAgIGlmIHJldmlld2VkCiAgICAgICAgZWxzZSAiT1JJR0lOQUxfUkVQT1JUX1ZFUklGSUVEX1ZJU1VBTF9QRU5ESU5HIiwKICAgICAgICBmaWxlcz1bc3RyKG91dHB1dCAvIG4pIGZvciBuIGluIHNvcnRlZChuYW1lcyldLAogICAgICAgIGJ1c2luZXNzX2NvZGVfbW9kaWZpZWQ9RmFsc2UsCiAgICAgICAgb3JpZ2luYWxfZmlsZXNfdmVyaWZpZWQ9bGVuKGV4cGVjdGVkKSwKICAgICAgICBwcm92aWRlcl9yZXF1ZXN0cz0wLAogICAgICAgIHdvcmtfbWVzc2FnZXNfc2VudD0wLAogICAgKQoKCmRlZiBjb21wbGV0ZShwcm9qZWN0LCBleHBlY3RlZCwgbWV0YSwgdmlzdWFsbHlfcmV2aWV3ZWQpOgogICAgcmVxdWlyZSh2aXN1YWxseV9yZXZpZXdlZCwgIlZJU1VBTF9SRVZJRVdfUkVRVUlSRUQiKQogICAgcmVzdWx0ID0gaW5zcGVjdChwcm9qZWN0LCBleHBlY3RlZCkKICAgIGlmIHJlc3VsdFsic3RhdHVzIl0gPT0gIk9SSUdJTkFMX1JFUE9SVF9BQ0NFUFRFRCI6CiAgICAgICAgcmV0dXJuIHJlc3VsdAogICAgbGF0ZXN0ID0ganNvbi5sb2FkcyhyZWFkKHByb2plY3QgLyAiTEFURVNULmpzb24iKSkKICAgIGRlbGl2ZXJ5ID0gcHJvamVjdCAvICJkZWxpdmVyaWVzIiAvIGxhdGVzdFsiYnVpbGRfaWQiXQogICAgcmVjZWlwdCA9IGpzb24ubG9hZHMocmVhZChkZWxpdmVyeSAvICJSRUNFSVBULmpzb24iKSkKICAgIGZvciBuYW1lLCBkaWdlc3QgaW4gcmVjZWlwdFsiYXJ0aWZhY3RzIl0uaXRlbXMoKToKICAgICAgICByZXF1aXJlKAogICAgICAgICAgICBzaGEocmVhZChwcm9qZWN0IC8gImVuZ2luZS9hcHAvb3V0cHV0IiAvIG5hbWUsIDEyICogMTAyNCAqIDEwMjQpKSA9PSBkaWdlc3QsCiAgICAgICAgICAgICJPUklHSU5BTF9PVVRQVVRfQ0hBTkdFRCIsCiAgICAgICAgKQogICAgZGF5ID0gbWV0YVsicHVibGljYXRpb25fZGF0ZSJdCiAgICBjbGFpbSA9IGpzb24ubG9hZHMoCiAgICAgICAgb3JpZ2luYWxfY29tbWFuZChwcm9qZWN0LCAid29ya2Zsb3dfZ2F0ZS5weSIsIFsiY2xhaW0iLCAiLS1kYXRlIiwgZGF5XSkKICAgICkKICAgIHJlcXVpcmUoY2xhaW0uZ2V0KCJzdGF0dXMiKSA9PSAiQ0xBSU1FRCIsICJPUklHSU5BTF9FWEVDVVRJT05fQUxSRUFEWV9DTEFJTUVEIikKICAgIG93bmVyID0gY2xhaW1bIm93bmVyIl0KICAgIGxvY2tmaWxlID0gcHJvamVjdCAvICJlbmdpbmUvYXBwL3N0YXRlIiAvIChkYXkgKyAiLmxvY2suanNvbiIpCiAgICByZXF1aXJlKGpzb24ubG9hZHMocmVhZChsb2NrZmlsZSkpLmdldCgib3duZXIiKSA9PSBvd25lciwgIk9SSUdJTkFMX09XTkVSX01JU1NJTkciKQogICAgY29tcGxldGVkID0ganNvbi5sb2FkcygKICAgICAgICBvcmlnaW5hbF9jb21tYW5kKAogICAgICAgICAgICBwcm9qZWN0LCAid29ya2Zsb3dfZ2F0ZS5weSIsIFsiY29tcGxldGUiLCAiLS1kYXRlIiwgZGF5LCAiLS1vd25lciIsIG93bmVyXQogICAgICAgICkKICAgICkKICAgIHJlcXVpcmUoY29tcGxldGVkLmdldCgic3RhdHVzIikgPT0gIkNPTVBMRVRFIiwgIk9SSUdJTkFMX0NPTVBMRVRJT05fRkFJTEVEIikKICAgIGluc3BlY3QocHJvamVjdCwgZXhwZWN0ZWQpCiAgICBvcmlnaW5hbF9jb21tYW5kKAogICAgICAgIHByb2plY3QsICJ3b3JrZmxvd19nYXRlLnB5IiwgWyJyZWxlYXNlIiwgIi0tZGF0ZSIsIGRheSwgIi0tb3duZXIiLCBvd25lcl0KICAgICkKICAgIHNhdmUoCiAgICAgICAgZGVsaXZlcnkgLyAiVklTVUFMLUFDQ0VQVEFOQ0UuanNvbiIsCiAgICAgICAgZGljdCgKICAgICAgICAgICAgcmVjZWlwdF9zaGEyNTY9bGF0ZXN0WyJyZWNlaXB0X3NoYTI1NiJdLAogICAgICAgICAgICB2aXN1YWxfcmV2aWV3ZWQ9VHJ1ZSwKICAgICAgICAgICAgb3JpZ2luYWxfY29tcGxldGVfdmVyaWZpZWQ9VHJ1ZSwKICAgICAgICAgICAgYWNjZXB0ZWRfYXQ9ZHQuZGF0ZXRpbWUubm93KFRaKS5pc29mb3JtYXQoKSwKICAgICAgICApLAogICAgKQogICAgcmV0dXJuIGluc3BlY3QocHJvamVjdCwgZXhwZWN0ZWQpCgoKZGVmIG1haW4oKToKICAgIHBhcnNlciA9IGFyZ3BhcnNlLkFyZ3VtZW50UGFyc2VyKGRlc2NyaXB0aW9uPV9fZG9jX18pCiAgICBwYXJzZXIuYWRkX2FyZ3VtZW50KAogICAgICAgICJhY3Rpb24iLAogICAgICAgIGNob2ljZXM9WwogICAgICAgICAgICAidmVyaWZ5LW9yaWdpbmFsIiwKICAgICAgICAgICAgImluaXQiLAogICAgICAgICAgICAiaW1wb3J0LW1hcmtldCIsCiAgICAgICAgICAgICJyZWZyZXNoLW1hcmtldCIsCiAgICAgICAgICAgICJjaGVjayIsCiAgICAgICAgICAgICJidWlsZCIsCiAgICAgICAgICAgICJpbnNwZWN0IiwKICAgICAgICAgICAgImNvbXBsZXRlIiwKICAgICAgICBdLAogICAgKQogICAgcGFyc2VyLmFkZF9hcmd1bWVudCgiLS1wcm9qZWN0IikKICAgIHBhcnNlci5hZGRfYXJndW1lbnQoCiAgICAgICAgIi0tZGF0ZSIsIGhlbHA9IlB1YmxpY2F0aW9uIGRhdGU7IG9taXR0ZWQgbWVhbnMgdG9kYXkgaW4gQXNpYS9TaGFuZ2hhaSIKICAgICkKICAgIHBhcnNlci5hZGRfYXJndW1lbnQoIi0tbWFya2V0LWNhY2hlIikKICAgIHBhcnNlci5hZGRfYXJndW1lbnQoIi0tYnJvd3NlciIpCiAgICBwYXJzZXIuYWRkX2FyZ3VtZW50KCItLXZpc3VhbC1yZXZpZXdlZCIsIGFjdGlvbj0ic3RvcmVfdHJ1ZSIpCiAgICBhcmdzID0gcGFyc2VyLnBhcnNlX2FyZ3MoKQogICAgcmVzdWx0ID0gZGljdChzdGF0dXM9IklOQ09NUExFVEUiLCBwcm92aWRlcl9yZXF1ZXN0cz0wLCB3b3JrX21lc3NhZ2VzX3NlbnQ9MCkKICAgIGxvY2sgPSBOb25lCiAgICB0cnk6CiAgICAgICAgZXhwZWN0ZWQgPSBvcmlnaW5hbF9maWxlcygpCiAgICAgICAgaWYgYXJncy5hY3Rpb24gPT0gInZlcmlmeS1vcmlnaW5hbCI6CiAgICAgICAgICAgIHJlc3VsdC51cGRhdGUoCiAgICAgICAgICAgICAgICBzdGF0dXM9Ik9SSUdJTkFMX1BBQ0tBR0VfVkVSSUZJRUQiLAogICAgICAgICAgICAgICAgZmlsZXM9bGVuKGV4cGVjdGVkKSwKICAgICAgICAgICAgICAgIGFyY2hpdmVfc2hhMjU2PUFSQ0hJVkVfU0hBLAogICAgICAgICAgICApCiAgICAgICAgZWxzZToKICAgICAgICAgICAgcmVxdWlyZShhcmdzLnByb2plY3QgaXMgbm90IE5vbmUsICJQUk9KRUNUX1JFUVVJUkVEIikKICAgICAgICAgICAgcHJvamVjdCA9IFBhdGgoYXJncy5wcm9qZWN0KS5hYnNvbHV0ZSgpCiAgICAgICAgICAgIHJlcXVpcmUoCiAgICAgICAgICAgICAgICBub3QgYW55KHAuaXNfc3ltbGluaygpIGZvciBwIGluIFtwcm9qZWN0LCAqcHJvamVjdC5wYXJlbnRzXSksCiAgICAgICAgICAgICAgICAiU1lNTElOS19SRUZVU0VEIiwKICAgICAgICAgICAgKQogICAgICAgICAgICByZXF1aXJlKAogICAgICAgICAgICAgICAgUk9PVC5yZXNvbHZlKCkgbm90IGluIHByb2plY3QucmVzb2x2ZSgpLnBhcmVudHMKICAgICAgICAgICAgICAgIGFuZCBwcm9qZWN0LnJlc29sdmUoKSAhPSBST09ULnJlc29sdmUoKSwKICAgICAgICAgICAgICAgICJXUklUQUJMRV9TRVNTSU9OX0NPUFlfUkVRVUlSRUQiLAogICAgICAgICAgICApCiAgICAgICAgICAgIGlmIGFyZ3MuYWN0aW9uID09ICJpbml0IjoKICAgICAgICAgICAgICAgIHJlc3VsdCA9IGluaXQocHJvamVjdCwgYXJncy5kYXRlLCBleHBlY3RlZCkKICAgICAgICAgICAgZWxzZToKICAgICAgICAgICAgICAgIGNhbmRpZGF0ZSA9IHByb2plY3QgLyAiLmVudHJ5LmxvY2siCiAgICAgICAgICAgICAgICB3aXRoIGNhbmRpZGF0ZS5vcGVuKCJ4IiwgZW5jb2Rpbmc9ImFzY2lpIikgYXMgc3RyZWFtOgogICAgICAgICAgICAgICAgICAgIHN0cmVhbS53cml0ZShzdHIob3MuZ2V0cGlkKCkpKQogICAgICAgICAgICAgICAgbG9jayA9IGNhbmRpZGF0ZQogICAgICAgICAgICAgICAgbWV0YSA9IHByb2plY3RfbWV0YShwcm9qZWN0LCBleHBlY3RlZCkKICAgICAgICAgICAgICAgIGlmIGFyZ3MuYWN0aW9uID09ICJpbXBvcnQtbWFya2V0IjoKICAgICAgICAgICAgICAgICAgICByZXF1aXJlKGFyZ3MubWFya2V0X2NhY2hlIGlzIG5vdCBOb25lLCAiTUFSS0VUX0NBQ0hFX1JFUVVJUkVEIikKICAgICAgICAgICAgICAgICAgICByZXN1bHQgPSBpbXBvcnRfbWFya2V0KHByb2plY3QsIGFyZ3MubWFya2V0X2NhY2hlLCBtZXRhKQogICAgICAgICAgICAgICAgZWxpZiBhcmdzLmFjdGlvbiA9PSAicmVmcmVzaC1tYXJrZXQiOgogICAgICAgICAgICAgICAgICAgIHJlc3VsdCA9IHJlZnJlc2hfbWFya2V0KHByb2plY3QsIG1ldGEpCiAgICAgICAgICAgICAgICBlbGlmIGFyZ3MuYWN0aW9uID09ICJjaGVjayI6CiAgICAgICAgICAgICAgICAgICAgb3JpZ2luYWxfY29tbWFuZCgKICAgICAgICAgICAgICAgICAgICAgICAgcHJvamVjdCwKICAgICAgICAgICAgICAgICAgICAgICAgInZhbGlkYXRlX2lucHV0cy5weSIsCiAgICAgICAgICAgICAgICAgICAgICAgIFsiLS1kYXRlIiwgbWV0YVsicHVibGljYXRpb25fZGF0ZSJdXSwKICAgICAgICAgICAgICAgICAgICApCiAgICAgICAgICAgICAgICAgICAgcmVzdWx0LnVwZGF0ZShzdGF0dXM9Ik9SSUdJTkFMX1ZBTElEQVRPUl9QQVNTRUQiKQogICAgICAgICAgICAgICAgZWxpZiBhcmdzLmFjdGlvbiA9PSAiYnVpbGQiOgogICAgICAgICAgICAgICAgICAgIHJlc3VsdCA9IGJ1aWxkX3JlcG9ydChwcm9qZWN0LCBtZXRhLCBleHBlY3RlZCwgYXJncy5icm93c2VyKQogICAgICAgICAgICAgICAgZWxpZiBhcmdzLmFjdGlvbiA9PSAiY29tcGxldGUiOgogICAgICAgICAgICAgICAgICAgIHJlc3VsdCA9IGNvbXBsZXRlKHByb2plY3QsIGV4cGVjdGVkLCBtZXRhLCBhcmdzLnZpc3VhbF9yZXZpZXdlZCkKICAgICAgICAgICAgICAgIGVsc2U6CiAgICAgICAgICAgICAgICAgICAgcmVzdWx0ID0gaW5zcGVjdChwcm9qZWN0LCBleHBlY3RlZCkKICAgICAgICAgICAgICAgIHZlcmlmeV90cmVlKHByb2plY3QgLyAiZW5naW5lIiwgZXhwZWN0ZWQsIFRydWUpCiAgICAgICAgb3JpZ2luYWxfZmlsZXMoKQogICAgZXhjZXB0IEV4Y2VwdGlvbiBhcyBleGM6CiAgICAgICAgcmVzdWx0ID0gZGljdCgKICAgICAgICAgICAgc3RhdHVzPSJJTkNPTVBMRVRFIiwKICAgICAgICAgICAgZXJyb3JfY2xhc3M9dHlwZShleGMpLl9fbmFtZV9fLAogICAgICAgICAgICBwcm92aWRlcl9yZXF1ZXN0cz0wLAogICAgICAgICAgICB3b3JrX21lc3NhZ2VzX3NlbnQ9MCwKICAgICAgICApCiAgICAgICAgY29kZSA9IHN0cihleGMpCiAgICAgICAgcmVzdWx0WyJlcnJvcl9jb2RlIl0gPSAoCiAgICAgICAgICAgIGNvZGUKICAgICAgICAgICAgaWYgY29kZQogICAgICAgICAgICBhbmQgbGVuKGNvZGUpIDwgMTIwCiAgICAgICAgICAgIGFuZCBhbGwoYyBpbiAiQUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVpfMDEyMzQ1Njc4OSIgZm9yIGMgaW4gY29kZSkKICAgICAgICAgICAgZWxzZSAiTE9DQUxfRkFJTFVSRV9SRURBQ1RFRCIKICAgICAgICApCiAgICBmaW5hbGx5OgogICAgICAgIGlmIGxvY2sgaXMgbm90IE5vbmU6CiAgICAgICAgICAgIGxvY2sudW5saW5rKCkKICAgIHByaW50KGpzb24uZHVtcHMocmVzdWx0LCBlbnN1cmVfYXNjaWk9RmFsc2UsIHNvcnRfa2V5cz1UcnVlKSkKICAgIHJldHVybiBpbnQocmVzdWx0WyJzdGF0dXMiXSA9PSAiSU5DT01QTEVURSIpCgoKaWYgX19uYW1lX18gPT0gIl9fbWFpbl9fIjoKICAgIHN5cy5leGl0KG1haW4oKSkK',validate=True)\n if hashlib.sha256(replacement).hexdigest()!='b250a6931dc0547b0d478894db628abb35db5cf282ef9d8021c2e6b1d19f9844':raise ValueError('OUTER_CANDIDATE_HASH')\n outer.write_bytes(replacement)\n spec=importlib.util.spec_from_file_location('entry',skill/'scripts/entry.py');entry=importlib.util.module_from_spec(spec);spec.loader.exec_module(entry)\n expected=entry.original_files()\n project=pathlib.Path('/tmp/energy-fixed-report-'+operation);entry.init(project,'2026-11-07',expected)\n meta=entry.project_meta(project,expected)\n result=entry.build_report(project,meta,expected,None)\n generated=project/'engine/app/output'\n for ext in ('html','json'):\n  actual=(generated/('油气与金融市场日报_2026-11-07.'+ext)).read_bytes()\n  reference=(skill/'upstream/reference'/('approved-2026-11-07.'+ext)).read_bytes()\n  # Original Windows outputs use CRLF; Linux uses LF. Only transport newlines differ.\n  if actual.replace(b'\\r\\n',b'\\n')!=reference.replace(b'\\r\\n',b'\\n'):raise ValueError('ORIGINAL_REFERENCE_MISMATCH')\n from PIL import Image\n png=generated/'油气与金融市场日报_2026-11-07.png'\n with Image.open(png) as im:\n  size=list(im.size)\n  if im.width!=780:raise ValueError('PNG_WIDTH')\n shutil.copyfile(png,release/'LINUX-SAMPLE.png')\n entry.original_files()\n print(json.dumps({'status':'STAGED_ORIGINAL_RENDER_PASSED','original_files':len(expected),'png_dimensions':size,'png_sha256':entry.sha(png.read_bytes()),'html_json_match_original_after_crlf_normalization':True,'linux_visual_review_pending':True,'provider_requests':0,'release_directory':str(release)},sort_keys=True))\n\nexcept Exception as exc:\n s=str(exc)\n print(json.dumps({'status':'STAGE_FAILED','error_class':type(exc).__name__,'error_code':s if s and len(s)<120 and all(c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789' for c in s) else 'DETAIL_REDACTED','provider_requests':0,'skill_published':False},sort_keys=True))\n"

PUBLISH = r"""
import importlib.util,json,os,pathlib,sys
op=sys.argv[1];root=pathlib.Path('/mnt/skills');release=root/('.energy-market-release-'+op);skill=release/'energy-market-daily';public=root/'public'
if any(p.is_symlink() for p in (root,public,release,skill)):raise ValueError('SYMLINK')
if (public/'energy-market-daily').exists() or (public/'energy-market-data').exists():raise ValueError('TARGET_EXISTS')
spec=importlib.util.spec_from_file_location('entry',skill/'scripts/entry.py');entry=importlib.util.module_from_spec(spec);spec.loader.exec_module(entry);entry.original_files()
for p in skill.rglob('*'):
 if p.is_symlink():raise ValueError('SYMLINK')
 os.chmod(p,0o755 if p.is_dir() else 0o644)
os.chmod(skill,0o755)
feed=public/'energy-market-data';feed.mkdir(mode=0o755)
os.rename(skill,public/'energy-market-daily')
print(json.dumps({'status':'PUBLIC_SKILL_INSTALLED','original_files':158,'feed_created':True}))
"""


class Install:
    def __init__(self):
        self.operation = uuid.uuid4().hex
        self.report = {
            "schema": "energy-market-skill-resume.v1",
            "tool_revision": "r1",
            "status": "INCOMPLETE",
            "operation": self.operation,
            "existing_deployment_mutations": 0,
            "work_messages_sent": 0,
            "schedule_created": False,
            "credentials_exported": False,
            "automatic_retry": False,
            "automatic_rollback": False,
            "new_resources_requested": [],
            "once_claimed": False,
            "skill_publish_requested": False,
            "network_policy_enforcement_proven": False,
            "business_acceptance_pending": True,
            "expected_image_id": EXPECTED_IMAGE_ID,
            "live_business_sandbox_required": False,
            "target_cached_runtime_verified": False,
        }
        self.commands = 0
        self.maintenance = PRIOR_POD
        self.pod_uid = PRIOR_POD_UID
        self.report["maintenance_uid"] = PRIOR_POD_UID
        self.sample_image_id = EXPECTED_IMAGE_ID

    def run_command(self, args, data=None, timeout=30, bound=8 * 1024 * 1024):
        self.commands += 1
        require(self.commands <= 180, "COMMAND_BUDGET")
        prefix = list(KUBE)
        if args and args[0] == "exec":
            prefix[2] = "--request-timeout=" + str(timeout) + "s"
        p = subprocess.run(
            prefix + args,
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        require(
            p.returncode == 0 and len(p.stdout) <= bound and len(p.stderr) <= 262144,
            "KUBECTL_COMMAND_FAILED",
        )
        return p.stdout

    def get(self, kind, name):
        return json.loads(self.run_command(["get", kind, name, "-o", "json"]))

    def absent(self, kind, name):
        require(
            not self.run_command(
                ["get", kind, name, "--ignore-not-found", "-o", "name"]
            ).strip(),
            "NEW_RESOURCE_EXISTS_" + kind.upper(),
        )

    def create(self, obj):
        obj.setdefault("metadata", {})["namespace"] = NAMESPACE
        self.report["new_resources_requested"].append(
            {"kind": obj["kind"], "name": obj["metadata"]["name"]}
        )
        self.save()
        return self.run_command(
            ["create", "-f", "-", "-o", "name"], json.dumps(obj).encode()
        )

    def save(self):
        if self.report["once_claimed"]:
            self.report["commands"] = self.commands
            tmp = REPORT_DIR / "REPORT.tmp"
            with tmp.open("w") as stream:
                json.dump(self.report, stream, sort_keys=True)
            os.replace(str(tmp), str(REPORT_DIR / "REPORT.json"))

    def protected(self):
        objects = {name: self.get("deployment", name) for name in BASELINE}
        for name, obj in objects.items():
            gen, image = BASELINE[name]
            require(
                obj["metadata"]["generation"] == gen
                and [c["image"] for c in obj["spec"]["template"]["spec"]["containers"]]
                == [image],
                "BASELINE_CHANGED_" + name.upper(),
            )
            require(stable_deployment(obj), "DEPLOYMENT_NOT_READY_" + name.upper())
        return {name: identity(obj) for name, obj in objects.items()}

    def prior(self):
        raw, _ = read_file(PRIOR_REPORT_DIR / "REPORT.json", 65536)
        require(sha(raw) == PRIOR_REPORT_SHA, "PRIOR_RECEIPT_CHANGED")
        report = json.loads(raw)
        require(
            report.get("operation") == PRIOR_OPERATION
            and report.get("maintenance_pod") == PRIOR_POD
            and report.get("maintenance_uid") == PRIOR_POD_UID
            and report.get("status") == "INCOMPLETE"
            and report.get("last_step") == "stage_original_linux_render"
            and report.get("new_resources_requested")
            == [{"kind": "Pod", "name": PRIOR_POD}]
            and report.get("once_claimed") is True
            and report.get("skill_publish_requested") is False
            and report.get("target_cached_runtime_verified") is True,
            "PRIOR_OPERATION_CHANGED",
        )
        self.report["prior_attempt"] = {
            "operation": PRIOR_OPERATION,
            "report_sha256": PRIOR_REPORT_SHA,
            "original_receipt_modified": False,
            "maintenance_pod_reused": True,
            "original_staging_preserved": True,
        }
        return report

    def pod_exec(self, code, args=(), data=None, timeout=240):
        pod = self.get("pod", self.maintenance)
        require(
            pod["metadata"]["uid"] == self.pod_uid
            and not pod["metadata"].get("deletionTimestamp"),
            "MAINTENANCE_IDENTITY",
        )
        result = self.run_command(
            [
                "exec",
                "-i",
                self.maintenance,
                "-c",
                "maintenance",
                "--",
                "python3",
                "-I",
                "-B",
                "-c",
                code,
            ]
            + list(args),
            data,
            timeout=timeout,
            bound=32768,
        )
        return json.loads(result)

    def wait_pod(self):
        self.report["last_step"] = "verify_target_cached_runtime"
        for _ in range(24):
            p = self.get("pod", self.maintenance)
            if self.pod_uid is None:
                self.pod_uid = p["metadata"]["uid"]
                self.report["maintenance_uid"] = self.pod_uid
            require(
                p["metadata"]["uid"] == self.pod_uid
                and not p["metadata"].get("deletionTimestamp")
                and p["metadata"].get("labels", {}).get("energy-market/maintenance")
                == PRIOR_OPERATION,
                "MAINTENANCE_UID_OR_OWNER",
            )
            spec = p["spec"]
            require(
                spec.get("nodeName") in (None, NODE)
                and len(spec["containers"]) == 1
                and spec["containers"][0]["name"] == "maintenance"
                and spec["containers"][0]["image"] == IMAGE
                and spec["containers"][0]["imagePullPolicy"] == "Never",
                "MAINTENANCE_PLACEMENT_OR_IMAGE",
            )
            status = p.get("status", {})
            cs = [
                c
                for c in status.get("containerStatuses", [])
                if c.get("name") == "maintenance"
            ]
            require(len(cs) <= 1, "MAINTENANCE_CONTAINER_STATUS")
            runtime = cs[0] if cs else {}
            waiting = runtime.get("state", {}).get("waiting", {}).get("reason")
            self.report["cache_probe"] = {
                "node": spec.get("nodeName"),
                "phase": status.get("phase"),
                "waiting_reason": waiting,
                "image_id": runtime.get("imageID"),
                "ready": runtime.get("ready", False),
                "image_pull_policy": "Never",
            }
            self.save()
            require(waiting != "ErrImageNeverPull", "TARGET_IMAGE_NOT_CACHED")
            require(
                status.get("phase") not in ("Failed", "Succeeded"),
                "MAINTENANCE_TERMINATED",
            )
            if runtime.get("ready"):
                require(
                    runtime.get("imageID") == self.sample_image_id,
                    "MAINTENANCE_IMAGE_ID_CHANGED",
                )
                require(
                    spec.get("nodeName") == NODE and status.get("phase") == "Running",
                    "MAINTENANCE_NOT_RUNNING_ON_TARGET",
                )
                self.report["target_cached_runtime_verified"] = True
                self.save()
                return
            time.sleep(5)
        raise ValueError("MAINTENANCE_NOT_READY")

    def install(self):
        check_host()
        previous = self.prior()
        require(not os.path.lexists(str(REPORT_DIR)), "PRIOR_OPERATION_REVIEW_REQUIRED")
        raw, _ = read_file(
            ROOT / "energy-market-daily-original-20261109-r1.zip", 6 * 1024 * 1024
        )
        code = base64.b64decode(COLLECTOR_CODE_B64, validate=True)
        require(sha(raw) == PACKAGE_SHA and sha(code) == CODE_SHA, "RELEASE_FILE_HASH")
        secret_bytes, mode = read_file(ROOT / "api-credential.private.json", 4096)
        require(mode.st_mode & 0o077 == 0, "CREDENTIAL_CHMOD_600_REQUIRED")
        secret = json.loads(secret_bytes)
        require(
            set(secret) == {"JKX_API_ENDPOINT", "JKX_API_PASSWORD"}
            and secret["JKX_API_ENDPOINT"]
            == "http://ip91.jkx.com.cn:6688/jkx_eecc.aspx",
            "CREDENTIAL_SCHEMA",
        )
        key = secret["JKX_API_PASSWORD"]
        require(isinstance(key, str) and 1 <= len(key) <= 1024, "CREDENTIAL_VALUE")
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            require(
                all(
                    key.encode() not in z.read(n)
                    for n in z.namelist()
                    if not n.endswith("/")
                ),
                "PRIVATE_MATERIAL_IN_PUBLIC_PACKAGE",
            )
        require(key.encode() not in code, "PRIVATE_MATERIAL_IN_COLLECTOR_CODE")
        self.report["last_step"] = "preflight"
        before = self.protected()
        require(before == previous["before"], "BASELINE_CHANGED_SINCE_FAILED_ATTEMPT")
        node = self.get("node", NODE)
        require(
            node["metadata"]["uid"] == NODE_UID
            and not node["spec"].get("unschedulable")
            and any(
                c["type"] == "Ready" and c["status"] == "True"
                for c in node["status"]["conditions"]
            ),
            "NODE_IDENTITY_OR_READY",
        )
        require(
            not any(
                t["effect"] in ("NoSchedule", "NoExecute")
                for t in node["spec"].get("taints", [])
            ),
            "NODE_HARD_TAINT",
        )
        pvc = self.get("pvc", PVC)
        require(
            pvc["metadata"]["uid"] == "5bd3d4ed-54df-45c7-8954-d32ff8e53ec9"
            and pvc["status"]["phase"] == "Bound",
            "SKILLS_VOLUME_IDENTITY",
        )
        self.report["runtime_identity_basis"] = (
            "OPERATOR_20261109_2316_12_READY_ORDINARY_CONTAINERS"
        )
        for kind, name in [
            ("secret", SECRET),
            ("configmap", CONFIG),
            ("deployment", DEPLOYMENT),
            ("service", DEPLOYMENT),
            ("networkpolicy", POLICY),
        ]:
            self.absent(kind, name)
        require(self.protected() == before, "BASELINE_CHANGED_DURING_PREFLIGHT")
        require(not REPORT_DIR.exists(), "PRIOR_OPERATION_REVIEW_REQUIRED")
        REPORT_DIR.mkdir(mode=0o700)
        self.report.update(
            once_claimed=True, before=before, maintenance_pod=self.maintenance
        )
        self.save()
        self.wait_pod()
        require(self.report["target_cached_runtime_verified"], "CACHE_PROOF_REQUIRED")
        self.report["last_step"] = "stage_original_linux_render"
        print(
            json.dumps({"phase": "VERIFY_ORIGINAL_REPORT_IN_EXISTING_ARM64_IMAGE"}),
            flush=True,
        )
        self.report["staging"] = self.pod_exec(
            STAGE, [self.operation, PACKAGE_SHA], raw
        )
        self.save()
        require(
            self.report["staging"].get("status") == "STAGED_ORIGINAL_RENDER_PASSED",
            "ORIGINAL_STAGE_NOT_ACCEPTED",
        )
        self.prior()
        require(self.protected() == before, "BASELINE_CHANGED_BEFORE_PUBLISH")
        self.report["last_step"] = "publish_new_skill"
        self.report["skill_publish_requested"] = True
        self.save()
        self.report["publication"] = self.pod_exec(PUBLISH, [self.operation])
        self.save()
        # Secret goes only to the new dedicated collector. Never use apply's annotation.
        self.report["last_step"] = "create_isolated_collector"
        self.create(
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": SECRET},
                "type": "Opaque",
                "immutable": True,
                "data": {
                    "credential.json": base64.b64encode(secret_bytes).decode("ascii")
                },
            }
        )
        self.create(
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": CONFIG},
                "immutable": True,
                "data": {"energy_market_collector.py": code.decode("utf-8")},
            }
        )
        for resource in build_resources(NODE_UID, self.operation, PRIOR_OPERATION):
            self.create(resource)
        print(json.dumps({"phase": "WAIT_NEW_COLLECTOR_ONLY"}), flush=True)
        for _ in range(36):
            d = self.get("deployment", DEPLOYMENT)
            if stable_deployment(d):
                break
            time.sleep(5)
        else:
            raise ValueError("COLLECTOR_NOT_READY")
        ps = json.loads(
            self.run_command(["get", "pods", "-l", "app=" + DEPLOYMENT, "-o", "json"])
        )["items"]
        live = [
            p
            for p in ps
            if not p["metadata"].get("deletionTimestamp")
            and p.get("status", {}).get("phase") == "Running"
        ]
        require(
            len(live) == 1
            and live[0]["spec"]["nodeName"] == NODE
            and live[0]["status"]["containerStatuses"][0]["imageID"]
            == self.sample_image_id,
            "COLLECTOR_RUNTIME_IDENTITY",
        )
        self.report["collector"] = identity(d)
        self.report["collector_pod_uid"] = live[0]["metadata"]["uid"]
        self.report["last_step"] = "verify_live_quote_path"
        probe = r"""
import datetime,importlib.util,json,time
spec=importlib.util.spec_from_file_location('entry','/mnt/skills/public/energy-market-daily/scripts/entry.py');e=importlib.util.module_from_spec(spec);spec.loader.exec_module(e);e.original_files()
day=(datetime.datetime.now(e.TZ).date()-datetime.timedelta(days=1)).isoformat()
for i in range(125):
 r=e.request_market(day)
 if r['status']!='COLLECTING':break
 time.sleep(5)
else:raise ValueError('COLLECTION_TIMEOUT')
if r['status'] not in ('READY','WAIT_DATA') or not r.get('target_response_validated'):raise ValueError('PROVIDER_TARGET_NOT_VALIDATED')
print(json.dumps(r,sort_keys=True))
"""
        self.report["market_probe"] = self.pod_exec(probe, timeout=660)
        self.report["last_step"] = "verify_preserved_services"
        after = self.protected()
        require(after == before, "PROTECTED_BASELINE_CHANGED")
        self.report["after"] = after
        # Delete only this operation's maintenance pod using an API UID precondition.
        current = self.get("pod", self.maintenance)
        require(current["metadata"]["uid"] == self.pod_uid, "MAINTENANCE_UID_CHANGED")
        self.report["last_step"] = "cleanup_owned_maintenance_pod"
        delete = {
            "apiVersion": "v1",
            "kind": "DeleteOptions",
            "preconditions": {"uid": self.pod_uid},
            "gracePeriodSeconds": 10,
        }
        self.run_command(
            [
                "delete",
                "--raw",
                "/api/v1/namespaces/" + NAMESPACE + "/pods/" + self.maintenance,
                "-f",
                "-",
            ],
            json.dumps(delete).encode(),
        )
        self.report["maintenance_delete_acknowledged"] = True
        for _ in range(10):
            if not self.run_command(
                ["get", "pod", self.maintenance, "--ignore-not-found", "-o", "name"]
            ).strip():
                self.report["maintenance_pod_absent"] = True
                break
            time.sleep(2)
        else:
            raise ValueError("MAINTENANCE_CLEANUP_PENDING")
        self.report.update(
            status="ENERGY_MARKET_SKILL_INSTALLED_RUNTIME_ACCEPTED",
            last_step="complete",
            production_dialogue_acceptance_pending=True,
            linux_visual_review_pending=True,
        )
        self.save()
        return self.report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume-energy-market-daily-once", action="store_true")
    args = parser.parse_args()
    require(args.resume_energy_market_daily_once, "EXPLICIT_RESUME_FLAG_REQUIRED")
    task = Install()
    try:
        task.install()
    except Exception as exc:
        task.report["error_class"] = type(exc).__name__
        value = str(exc)
        task.report["error_code"] = (
            value
            if value
            and len(value) < 100
            and all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789" for c in value)
            else "LOCAL_FAILURE_REDACTED"
        )
        task.save()
    task.report["observed_at"] = datetime.datetime.now(
        datetime.timezone.utc
    ).isoformat()
    task.report["report_path"] = (
        str(REPORT_DIR / "REPORT.json") if task.report["once_claimed"] else None
    )
    task.save()
    print(json.dumps(task.report, sort_keys=True))
    return int(task.report["status"] == "INCOMPLETE")


if __name__ == "__main__":
    sys.exit(main())
