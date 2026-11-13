"""Gateway 414 -> 415: only scheduled-run budget 300 -> 10000.

Creates one new immutable ConfigMap, then one full-spec CAS reference patch.
No automatic retry/rollback. --verify-readonly resumes verification only.
Existing runs keep their captured budgets. Normal application startup and
already-enabled background schedules may access DB/Redis/providers/Work.
Python 3.7-compatible operator; YAML qualification uses existing Gateway Python.
"""
import argparse
import base64
import copy
import datetime
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid

CHILD_ASSET = {'base64': 'IiIiUmVhZC1vbmx5IFlBTUwvc291cmNlIHF1YWxpZmljYXRpb24gaW4gYW4gZXhpc3RpbmcgR2F0ZXdheTsgbmV2ZXIgaW1wb3J0IGFwcC4iIiIKaW1wb3J0IGJhc2U2NAppbXBvcnQgaGFzaGxpYgppbXBvcnQgaW1wb3J0bGliLm1ldGFkYXRhCmltcG9ydCBqc29uCmltcG9ydCBvcwpmcm9tIHBhdGhsaWIgaW1wb3J0IFBhdGgKaW1wb3J0IHN0YXQKaW1wb3J0IHN5cwoKQ09ORklHID0gJy9hcHAvYmFja2VuZC9jb25maWcueWFtbCcKU09VUkNFX1BBVEhTID0gZnJvemVuc2V0KCgKICAgICcvYXBwL2JhY2tlbmQvcGFja2FnZXMvaGFybmVzcy9kZWVyZmxvdy9jb25maWcvYXBwX2NvbmZpZy5weScsCiAgICAnL2FwcC9iYWNrZW5kL3BhY2thZ2VzL2hhcm5lc3MvZGVlcmZsb3cvY29uZmlnL3NjaGVkdWxlcl9jb25maWcucHknLAogICAgJy9hcHAvYmFja2VuZC9hcHAvZ2F0ZXdheS9zZXJ2aWNlcy5weScsCiAgICAnL2FwcC9iYWNrZW5kL3BhY2thZ2VzL2hhcm5lc3MvZGVlcmZsb3cvYWdlbnRzL2xlYWRfYWdlbnQvYWdlbnQucHknLAogICAgJy9hcHAvYmFja2VuZC9hcHAvY2hhbm5lbHMvbWFuYWdlci5weScsCiAgICAnL2FwcC9iYWNrZW5kL2FwcC9pbnRlZ3JhdGlvbnMvd29yay9ub3RpZmljYXRpb25zLnB5JywKICAgICcvYXBwL2JhY2tlbmQvYXBwL3NjaGVkdWxlci93b3JrX25vdGlmaWNhdGlvbl9kZWxpdmVyeS5weScsCiAgICAnL2FwcC9iYWNrZW5kL2FwcC9zY2hlZHVsZXIvd29ya19ub3RpZmljYXRpb25zLnB5JywKICAgICcvYXBwL2JhY2tlbmQvcGFja2FnZXMvaGFybmVzcy9kZWVyZmxvdy90b29scy9idWlsdGlucy9wcmVzZW50X2ZpbGVfdG9vbC5weScsCiAgICAnL2FwcC9iYWNrZW5kL3BhY2thZ2VzL2hhcm5lc3MvZGVlcmZsb3cvdXRpbHMvY29udmVyc2F0aW9uX3VybC5weScsCiAgICAnL2FwcC9iYWNrZW5kL2FwcC9nYXRld2F5L3JvdXRlcnMvdXBsb2Fkcy5weScsCiAgICAnL2FwcC9iYWNrZW5kL2FwcC9nYXRld2F5L3VwbG9hZF9kb2N1bWVudF9tZW1vcnlfZ3VhcmQucHknLAogICAgJy9ldGMvZGVlcmZsb3ctb3BlbnNzbC9vcGVuc3NsLmNuZicsCikpClBBQ0tBR0VTID0geydsYW5nY2hhaW4nOiAnMS4yLjE1JywgJ2xhbmdjaGFpbi1jb3JlJzogJzEuMy4zJywgJ2xhbmdncmFwaCc6ICcxLjEuOSd9CgoKY2xhc3MgUmVmdXNlZChWYWx1ZUVycm9yKToKICAgIHBhc3MKCgpkZWYgbmVlZChvaywgY29kZSk6CiAgICBpZiBub3Qgb2s6CiAgICAgICAgcmFpc2UgUmVmdXNlZChjb2RlKQoKCmRlZiBkaWdlc3QocmF3KToKICAgIHJldHVybiBoYXNobGliLnNoYTI1NihyYXcpLmhleGRpZ2VzdCgpCgoKZGVmIGF1ZGl0KGV2ZW50LCBhcmdzKToKICAgIGlmIGV2ZW50ID09ICdvcGVuJzoKICAgICAgICBtb2RlID0gYXJnc1sxXSBpZiBsZW4oYXJncykgPiAxIGVsc2UgTm9uZQogICAgICAgIGZsYWdzID0gYXJnc1syXSBpZiBsZW4oYXJncykgPiAyIGVsc2UgMAogICAgICAgIG5lZWQobm90IChpc2luc3RhbmNlKG1vZGUsIHN0cikgYW5kIGFueSh4IGluIG1vZGUgZm9yIHggaW4gJ3dheCsnKSksCiAgICAgICAgICAgICAnQ0hJTERfV1JJVEVfQkxPQ0tFRCcpCiAgICAgICAgbmVlZChub3QgKGlzaW5zdGFuY2UoZmxhZ3MsIGludCkgYW5kIGZsYWdzICYgKG9zLk9fV1JPTkxZIHwgb3MuT19SRFdSIHwgb3MuT19DUkVBVCB8IG9zLk9fVFJVTkMgfCBvcy5PX0FQUEVORCkpLAogICAgICAgICAgICAgJ0NISUxEX1dSSVRFX0JMT0NLRUQnKQogICAgaWYgZXZlbnQuc3RhcnRzd2l0aCgoJ3NvY2tldC4nLCAnc3VicHJvY2Vzcy4nLCAnb3MuZXhlYycsICdvcy5zcGF3bicpKSBvciBldmVudCBpbiAoCiAgICAgICAgJ29zLnN5c3RlbScsICdvcy5yZW1vdmUnLCAnb3MucmVuYW1lJywgJ29zLm1rZGlyJywgJ29zLnJtZGlyJywgJ29zLmNobW9kJywgJ29zLmNob3duJywgJ29zLmxpbmsnLCAnb3Muc3ltbGluaycsCiAgICApOgogICAgICAgIHJhaXNlIFJlZnVzZWQoJ0NISUxEX1NJREVfRUZGRUNUX0JMT0NLRUQnKQoKCmRlZiBib3VuZGVkX3JlYWQocGF0aCwgbGltaXQpOgogICAgbmVlZChwYXRoID09IENPTkZJRyBvciBwYXRoIGluIFNPVVJDRV9QQVRIUywgJ0NISUxEX1BBVEhfU0NPUEUnKQogICAgdGFyZ2V0ID0gUGF0aChwYXRoKQogICAgIyBBIGRpcmVjdG9yeS1wcm9qZWN0ZWQgQ29uZmlnTWFwIGhhcyBLdWJlcm5ldGVzJyBvd24gLi5kYXRhIHN5bWxpbmsuCiAgICAjIEl0IGlzIHBlcm1pdHRlZCBvbmx5IGluc2lkZSB0aGlzIGZpeGVkIE9wZW5TU0wgcHJvamVjdGlvbiwgbm90IGVsc2V3aGVyZS4KICAgIGlmIHBhdGggPT0gJy9ldGMvZGVlcmZsb3ctb3BlbnNzbC9vcGVuc3NsLmNuZic6CiAgICAgICAgcmVzb2x2ZWQgPSB0YXJnZXQucmVzb2x2ZShzdHJpY3Q9VHJ1ZSkKICAgICAgICBuZWVkKFBhdGgoJy9ldGMvZGVlcmZsb3ctb3BlbnNzbCcpIGluIHJlc29sdmVkLnBhcmVudHMsICdPUEVOU1NMX0xJTktfU0NPUEUnKQogICAgICAgIHRhcmdldCA9IHJlc29sdmVkCiAgICBlbHNlOgogICAgICAgIG5lZWQobm90IHRhcmdldC5pc19zeW1saW5rKCksICdGSVhFRF9GSUxFX1NZTUxJTksnKQogICAgZmQgPSBvcy5vcGVuKHN0cih0YXJnZXQpLCBvcy5PX1JET05MWSB8IG9zLk9fTk9GT0xMT1cgfCBvcy5PX05PTkJMT0NLKQogICAgdHJ5OgogICAgICAgIGJlZm9yZSA9IG9zLmZzdGF0KGZkKQogICAgICAgIG5lZWQoc3RhdC5TX0lTUkVHKGJlZm9yZS5zdF9tb2RlKSBhbmQgMCA8IGJlZm9yZS5zdF9zaXplIDw9IGxpbWl0LCAnRklYRURfRklMRV9CT1VORCcpCiAgICAgICAgcmF3ID0gb3MucmVhZChmZCwgbGltaXQgKyAxKQogICAgICAgIGFmdGVyID0gb3MuZnN0YXQoZmQpCiAgICAgICAgc2lnbmF0dXJlID0gbGFtYmRhIGl0ZW06IChpdGVtLnN0X2RldiwgaXRlbS5zdF9pbm8sIGl0ZW0uc3Rfc2l6ZSwgaXRlbS5zdF9tdGltZV9ucywgaXRlbS5zdF9jdGltZV9ucykKICAgICAgICBuZWVkKGxlbihyYXcpID09IGJlZm9yZS5zdF9zaXplIGFuZCBzaWduYXR1cmUoYmVmb3JlKSA9PSBzaWduYXR1cmUoYWZ0ZXIpID09IHNpZ25hdHVyZSh0YXJnZXQuc3RhdCgpKSwKICAgICAgICAgICAgICdGSVhFRF9GSUxFX0NIQU5HRUQnKQogICAgICAgIHJldHVybiByYXcKICAgIGZpbmFsbHk6CiAgICAgICAgb3MuY2xvc2UoZmQpCgoKZGVmIHBhcnNlX3lhbWwocmF3KToKICAgIGltcG9ydCB5YW1sCgogICAgY2xhc3MgU3RyaWN0TG9hZGVyKHlhbWwuU2FmZUxvYWRlcik6CiAgICAgICAgcGFzcwoKICAgIGRlZiBtYXBwaW5nKGxvYWRlciwgbm9kZSwgZGVlcD1GYWxzZSk6CiAgICAgICAgbmVlZChub2RlLnRhZyA9PSAndGFnOnlhbWwub3JnLDIwMDI6bWFwJywgJ1lBTUxfTUFQUElOR19UQUcnKQogICAgICAgIHJlc3VsdCA9IHt9CiAgICAgICAgZm9yIGtleV9ub2RlLCB2YWx1ZV9ub2RlIGluIG5vZGUudmFsdWU6CiAgICAgICAgICAgIG5lZWQoa2V5X25vZGUudGFnICE9ICd0YWc6eWFtbC5vcmcsMjAwMjptZXJnZScsICdZQU1MX01FUkdFX1JFRlVTRUQnKQogICAgICAgICAgICBrZXkgPSBsb2FkZXIuY29uc3RydWN0X29iamVjdChrZXlfbm9kZSwgZGVlcD1kZWVwKQogICAgICAgICAgICBuZWVkKGlzaW5zdGFuY2Uoa2V5LCBzdHIpIGFuZCBrZXkgbm90IGluIHJlc3VsdCwgJ1lBTUxfRFVQTElDQVRFX09SX05PTlNUUklOR19LRVknKQogICAgICAgICAgICByZXN1bHRba2V5XSA9IGxvYWRlci5jb25zdHJ1Y3Rfb2JqZWN0KHZhbHVlX25vZGUsIGRlZXA9ZGVlcCkKICAgICAgICByZXR1cm4gcmVzdWx0CgogICAgU3RyaWN0TG9hZGVyLmFkZF9jb25zdHJ1Y3RvcigndGFnOnlhbWwub3JnLDIwMDI6bWFwJywgbWFwcGluZykKICAgICMgTm8gYWxpYXNlczogYXZvaWRzIGN5Y2xpYyBjb21wYXJpc29uIGFuZCBpbXBsaWNpdCBlZGl0cyB0aHJvdWdoIHNoYXJlZCBub2Rlcy4KICAgIGNsYXNzIE5vQWxpYXNlcyhTdHJpY3RMb2FkZXIpOgogICAgICAgIGRlZiBjb21wb3NlX25vZGUoc2VsZiwgcGFyZW50LCBpbmRleCk6CiAgICAgICAgICAgIG5lZWQobm90IHNlbGYuY2hlY2tfZXZlbnQoeWFtbC5BbGlhc0V2ZW50KSwgJ1lBTUxfQUxJQVNfUkVGVVNFRCcpCiAgICAgICAgICAgIHJldHVybiBzdXBlcigpLmNvbXBvc2Vfbm9kZShwYXJlbnQsIGluZGV4KQoKICAgIHBhcnNlZCA9IHlhbWwubG9hZChyYXcuZGVjb2RlKCd1dGYtOCcpLCBMb2FkZXI9Tm9BbGlhc2VzKQogICAgbmVlZChpc2luc3RhbmNlKHBhcnNlZCwgZGljdCksICdZQU1MX1JPT1RfTUFQUElOR19SRVFVSVJFRCcpCiAgICByZXR1cm4gcGFyc2VkCgoKZGVmIGNoZWNrX2NvbmZpZyhyYXcsIGV4cGVjdGVkKToKICAgIG5lZWQobGVuKHJhdykgPT0gZXhwZWN0ZWRbJ2J5dGVzJ10gYW5kIGRpZ2VzdChyYXcpID09IGV4cGVjdGVkWydzaGEyNTYnXSwgJ0NPTkZJR19IQVNIX0NIQU5HRUQnKQogICAgcGFyc2VkID0gcGFyc2VfeWFtbChyYXcpCiAgICBzY2hlZHVsZXIgPSBwYXJzZWQuZ2V0KCdzY2hlZHVsZXInKQogICAgbmVlZChpc2luc3RhbmNlKHNjaGVkdWxlciwgZGljdCksICdTQ0hFRFVMRVJfTUFQUElOR19SRVFVSVJFRCcpCiAgICBidWRnZXQgPSBzY2hlZHVsZXIuZ2V0KCdyZWN1cnNpb25fbGltaXQnKQogICAgbWF4aW11bSA9IHBhcnNlZC5nZXQoJ21heF9yZWN1cnNpb25fbGltaXQnKQogICAgbmVlZCh0eXBlKGJ1ZGdldCkgaXMgaW50IGFuZCBidWRnZXQgPT0gZXhwZWN0ZWRbJ3NjaGVkdWxlcl9yZWN1cnNpb25fbGltaXQnXSwgJ1NDSEVEVUxFUl9CVURHRVRfQ0hBTkdFRCcpCiAgICBuZWVkKHR5cGUobWF4aW11bSkgaXMgaW50IGFuZCBtYXhpbXVtID09IDEwMDAwLCAnTUFYX1JFQ1VSU0lPTl9MSU1JVF9DSEFOR0VEJykKICAgIHJldHVybiBwYXJzZWQKCgpkZWYgb2JzZXJ2ZShwYXlsb2FkKToKICAgIG5lZWQoaXNpbnN0YW5jZShwYXlsb2FkLCBkaWN0KSBhbmQgcGF5bG9hZC5nZXQoJ21vZGUnKSBpbiAoJ3F1YWxpZnknLCAndmVyaWZ5JyksICdDSElMRF9NT0RFJykKICAgIG5lZWQoc2V0KHBheWxvYWQpID09ICh7J21vZGUnLCAnY29uZmlnJywgJ2NhbmRpZGF0ZScsICdzb3VyY2VzJ30gaWYgcGF5bG9hZFsnbW9kZSddID09ICdxdWFsaWZ5JwogICAgICAgICAgICAgICAgICAgICAgICAgIGVsc2Ugeydtb2RlJywgJ2NvbmZpZycsICdzb3VyY2VzJ30pLCAnQ0hJTERfUEFZTE9BRF9LRVlTJykKICAgIGV4cGVjdGVkX3NvdXJjZXMgPSBwYXlsb2FkWydzb3VyY2VzJ10KICAgIG5lZWQoaXNpbnN0YW5jZShleHBlY3RlZF9zb3VyY2VzLCBkaWN0KSBhbmQgc2V0KGV4cGVjdGVkX3NvdXJjZXMpID09IFNPVVJDRV9QQVRIUywgJ1NPVVJDRV9TRVRfQ0hBTkdFRCcpCiAgICByYXcgPSBib3VuZGVkX3JlYWQoQ09ORklHLCA2NTUzNikKICAgIGJlZm9yZSA9IGNoZWNrX2NvbmZpZyhyYXcsIHBheWxvYWRbJ2NvbmZpZyddKQogICAgcmVzdWx0ID0geydzY2hlbWEnOiAnc2NoZWR1bGVyLWJ1ZGdldC1xdWFsaWZpY2F0aW9uLWNoaWxkLnYxJywgJ3N0YXR1cyc6ICdJTkNPTVBMRVRFJywKICAgICAgICAgICAgICAnYXBwbGljYXRpb25faW1wb3J0ZWQnOiBGYWxzZSwgJ3Byb3ZpZGVyX3JlcXVlc3RzJzogMCwgJ2RhdGFiYXNlX2FjY2Vzcyc6IEZhbHNlLAogICAgICAgICAgICAgICdwZXJzaXN0ZW50X2ZpbGVzX21vZGlmaWVkJzogRmFsc2UsICdyYXdfY29uZmlndXJhdGlvbl9leHBvcnRlZCc6IEZhbHNlLAogICAgICAgICAgICAgICdjcmVkZW50aWFsc19leHBvcnRlZCc6IEZhbHNlLCAnY29uZmlnX3NoYTI1Nic6IGRpZ2VzdChyYXcpLAogICAgICAgICAgICAgICdzY2hlZHVsZXJfcmVjdXJzaW9uX2xpbWl0JzogcGF5bG9hZFsnY29uZmlnJ11bJ3NjaGVkdWxlcl9yZWN1cnNpb25fbGltaXQnXSwKICAgICAgICAgICAgICAnbWF4X3JlY3Vyc2lvbl9saW1pdCc6IDEwMDAwfQogICAgaWYgcGF5bG9hZFsnbW9kZSddID09ICdxdWFsaWZ5JzoKICAgICAgICBjYW5kaWRhdGUgPSBwYXlsb2FkWydjYW5kaWRhdGUnXQogICAgICAgIG5lZWQoc2V0KGNhbmRpZGF0ZSkgPT0geydiYXNlNjQnLCAnYnl0ZXMnLCAnc2hhMjU2JywgJ3NjaGVkdWxlcl9yZWN1cnNpb25fbGltaXQnfSwgJ0NBTkRJREFURV9LRVlTJykKICAgICAgICBuZXh0X3JhdyA9IGJhc2U2NC5iNjRkZWNvZGUoY2FuZGlkYXRlWydiYXNlNjQnXSwgdmFsaWRhdGU9VHJ1ZSkKICAgICAgICBhZnRlciA9IGNoZWNrX2NvbmZpZyhuZXh0X3JhdywgY2FuZGlkYXRlKQogICAgICAgIGFmdGVyWydzY2hlZHVsZXInXVsncmVjdXJzaW9uX2xpbWl0J10gPSAzMDAKICAgICAgICBuZWVkKGJlZm9yZSA9PSBhZnRlciBhbmQgYmVmb3JlWydzY2hlZHVsZXInXVsncmVjdXJzaW9uX2xpbWl0J10gPT0gMzAwLCAnWUFNTF9TRU1BTlRJQ19TQ09QRV9FWENFRURFRCcpCiAgICAgICAgbmVlZChjYW5kaWRhdGVbJ3NjaGVkdWxlcl9yZWN1cnNpb25fbGltaXQnXSA9PSAxMDAwMCwgJ0NBTkRJREFURV9CVURHRVRfUkVRVUlSRUQnKQogICAgICAgIHJlc3VsdC51cGRhdGUoY2FuZGlkYXRlX3NoYTI1Nj1kaWdlc3QobmV4dF9yYXcpLCBjYW5kaWRhdGVfc2NoZWR1bGVyX3JlY3Vyc2lvbl9saW1pdD0xMDAwMCwKICAgICAgICAgICAgICAgICAgICAgIHNlbWFudGljX2NoYW5nZV9vbmx5X3NjaGVkdWxlcl9yZWN1cnNpb25fbGltaXQ9VHJ1ZSkKICAgIHZlcnNpb25zID0ge25hbWU6IGltcG9ydGxpYi5tZXRhZGF0YS52ZXJzaW9uKG5hbWUpIGZvciBuYW1lIGluIFBBQ0tBR0VTfQogICAgbmVlZCh2ZXJzaW9ucyA9PSBQQUNLQUdFUywgJ0dBVEVXQVlfTElCUkFSWV9WRVJTSU9OU19DSEFOR0VEJykKICAgIHJlc3VsdFsndmVyc2lvbnMnXSA9IHZlcnNpb25zCiAgICByb3dzLCB0b3RhbCA9IHt9LCAwCiAgICBmb3IgcGF0aCBpbiBzb3J0ZWQoU09VUkNFX1BBVEhTKToKICAgICAgICBjb250ZW50ID0gYm91bmRlZF9yZWFkKHBhdGgsIDEwNDg1NzYpCiAgICAgICAgdG90YWwgKz0gbGVuKGNvbnRlbnQpCiAgICAgICAgbmVlZCh0b3RhbCA8PSA0MTk0MzA0LCAnU09VUkNFX1RPVEFMX0JPVU5EJykKICAgICAgICByb3cgPSB7J2J5dGVzJzogbGVuKGNvbnRlbnQpLCAnc2hhMjU2JzogZGlnZXN0KGNvbnRlbnQpfQogICAgICAgIG5lZWQocm93ID09IGV4cGVjdGVkX3NvdXJjZXNbcGF0aF0sICdGSVhFRF9TT1VSQ0VfSEFTSF9DSEFOR0VEJykKICAgICAgICByb3dzW3BhdGhdID0gcm93CiAgICBuZWVkKGJvdW5kZWRfcmVhZChDT05GSUcsIDY1NTM2KSA9PSByYXcsICdDT05GSUdfUkVBREJBQ0tfQ0hBTkdFRCcpCiAgICByZXN1bHQudXBkYXRlKHN0YXR1cz0nU0NIRURVTEVSX0JVREdFVF9SRUFET05MWV9RVUFMSUZJRUQnLCBzb3VyY2VfZmlsZXNfdmVyaWZpZWQ9bGVuKHJvd3MpLAogICAgICAgICAgICAgICAgICBzb3VyY2VfaGFzaGVzPXJvd3MsIHN0YWJsZV9yZWFkYmFjaz1UcnVlKQogICAgcmV0dXJuIHJlc3VsdAoKCmRlZiBtYWluKCk6CiAgICByZXN1bHQgPSB7J3NjaGVtYSc6ICdzY2hlZHVsZXItYnVkZ2V0LXF1YWxpZmljYXRpb24tY2hpbGQudjEnLCAnc3RhdHVzJzogJ0lOQ09NUExFVEUnLAogICAgICAgICAgICAgICdhcHBsaWNhdGlvbl9pbXBvcnRlZCc6IEZhbHNlLCAncHJvdmlkZXJfcmVxdWVzdHMnOiAwLCAnZGF0YWJhc2VfYWNjZXNzJzogRmFsc2UsCiAgICAgICAgICAgICAgJ3BlcnNpc3RlbnRfZmlsZXNfbW9kaWZpZWQnOiBGYWxzZSwgJ2NyZWRlbnRpYWxzX2V4cG9ydGVkJzogRmFsc2UsICdyYXdfZXJyb3JzX2V4cG9ydGVkJzogRmFsc2V9CiAgICB0cnk6CiAgICAgICAgcmF3ID0gc3lzLnN0ZGluLmJ1ZmZlci5yZWFkKDI2MjE0NSkKICAgICAgICBuZWVkKDAgPCBsZW4ocmF3KSA8PSAyNjIxNDQsICdDSElMRF9JTlBVVF9CT1VORCcpCiAgICAgICAgcGF5bG9hZCA9IGpzb24ubG9hZHMocmF3KQogICAgICAgIHN5cy5hZGRhdWRpdGhvb2soYXVkaXQpCiAgICAgICAgcmVzdWx0ID0gb2JzZXJ2ZShwYXlsb2FkKQogICAgZXhjZXB0IEV4Y2VwdGlvbiBhcyBleGM6CiAgICAgICAgcmVzdWx0WydlcnJvcl9jbGFzcyddID0gdHlwZShleGMpLl9fbmFtZV9fCiAgICAgICAgcmVzdWx0WydlcnJvcl9jb2RlJ10gPSBzdHIoZXhjKSBpZiBpc2luc3RhbmNlKGV4YywgUmVmdXNlZCkgZWxzZSAnQ0hJTERfUVVBTElGSUNBVElPTl9GQUlMRUQnCiAgICBwcmludChqc29uLmR1bXBzKHJlc3VsdCwgc29ydF9rZXlzPVRydWUpLCBmbHVzaD1UcnVlKQogICAgcmV0dXJuIDAgaWYgcmVzdWx0WydzdGF0dXMnXSA9PSAnU0NIRURVTEVSX0JVREdFVF9SRUFET05MWV9RVUFMSUZJRUQnIGVsc2UgMQoKCmlmIF9fbmFtZV9fID09ICdfX21haW5fXyc6CiAgICBzeXMuZXhpdChtYWluKCkpCg==', 'sha256': 'b052131a1e37f7f3e38e3f64ba1cefa8735337011d50c1e104614996ec919d81'}
NS = 'deer-flow'
DEPLOYMENT_UID = 'a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1'
BEFORE_GENERATION = 414
BEFORE_SPEC_SHA = '542846c6041c25b7ba94073c4b1b58f883910b8042d00951e33455994df76877'
OLD_CM = 'deer-flow-budget-10000-bf39d16f673d2259'
OLD_CONFIG_SHA = 'bf39d16f673d2259a0430357e7a9a435482add27194aa947a0e34cdd594f1fb7'
OLD_CONFIG_BYTES = 15990
OPENSSL_CM = 'deer-flow-gateway-openssl-compat'
OPENSSL_SHA = '20130c1b277e0a5fb79a27af7dc35be38162a6e086aa91e45b4f249d73c61604'
UPLOAD_CM = 'gateway-conversation-stream-12dc02bb473d5ad9'
LINK_CM = 'gateway-energy-links-b6fb1ab44d10'
CONTROL = Path('/var/lib/portal-scheduler-budget-20261112-r2')
LABEL = 'portal.scheduler-budget/operation'
KUBE = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=20s', '-n', NS]
SAFE_PATH = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
TOTAL_SECONDS, ROLLOUT_SECONDS = 7200, 6000
CORE_SOURCES = {
    '/app/backend/packages/harness/deerflow/config/app_config.py': (31735, 'ab3056db7657d4ee9de7fbd754f59f4d8e14be78f2535b049047d6d03cc863ad'),
    '/app/backend/packages/harness/deerflow/config/scheduler_config.py': (972, '4495e7c800fa602dd7a0bf85ef15768030abf6bf4f2095e654330a56db1070cb'),
    '/app/backend/app/gateway/services.py': (53637, '6e46773b1d9ae19f7d15bd01b7ac51dd85f56d4da1df6c6773df8f78d490d377'),
    '/app/backend/packages/harness/deerflow/agents/lead_agent/agent.py': (41101, 'ec108a4b8cc9c608ba790e71bc5a1c34bbd33cd5241503c7a4ddba7a7e5e0d30'),
}
OVERLAYS = {
    LINK_CM: {
        'manager.py': '/app/backend/app/channels/manager.py',
        'notifications.py': '/app/backend/app/integrations/work/notifications.py',
        'work_notification_delivery.py': '/app/backend/app/scheduler/work_notification_delivery.py',
        'work_notifications.py': '/app/backend/app/scheduler/work_notifications.py',
        'present_file_tool.py': '/app/backend/packages/harness/deerflow/tools/builtins/present_file_tool.py',
        'conversation_url.py': '/app/backend/packages/harness/deerflow/utils/conversation_url.py',
    },
    UPLOAD_CM: {
        'uploads.py': '/app/backend/app/gateway/routers/uploads.py',
        'upload_document_memory_guard.py': '/app/backend/app/gateway/upload_document_memory_guard.py',
    },
    OPENSSL_CM: {'openssl.cnf': '/etc/deerflow-openssl/openssl.cnf'},
}


class Refused(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise Refused(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def safe(path):
    path = Path(path)
    need(path.is_absolute() and '..' not in path.parts, 'PRIVATE_PATH')
    for part in (path,) + tuple(path.parents):
        try:
            state = part.lstat()
        except FileNotFoundError:
            continue
        need(not stat.S_ISLNK(state.st_mode) and not getattr(state, 'st_file_attributes', 0) & 0x400,
             'PRIVATE_PATH_LINK')
    return path


def private_write(path, raw):
    path = safe(path)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(fd)
    finally:
        os.close(fd)


def private_read(path, maximum=4194304):
    path = safe(path)
    fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 < before.st_size <= maximum,
             'PRIVATE_FILE_BOUND')
        if os.name == 'posix':
            need(before.st_uid == 0 and stat.S_IMODE(before.st_mode) == 0o600, 'PRIVATE_FILE_OWNER_MODE')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(maximum + 1)
        after = os.fstat(fd)
        stamp = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        need(len(raw) == before.st_size and stamp(before) == stamp(after) == stamp(path.stat()), 'PRIVATE_FILE_CHANGED')
        return raw
    finally:
        os.close(fd)


def load_child():
    need(set(CHILD_ASSET) == {'base64', 'sha256'}, 'CHILD_ASSET_NOT_SEALED')
    need(isinstance(CHILD_ASSET['base64'], str) and len(CHILD_ASSET['base64']) <= 131072,
         'CHILD_ASSET_BOUND')
    raw = base64.b64decode(CHILD_ASSET['base64'], validate=True)
    need(0 < len(raw) <= 65536 and sha(raw) == CHILD_ASSET['sha256'], 'CHILD_ASSET_HASH')
    return raw.decode('utf-8')


def identity(obj):
    meta = obj['metadata']
    return {'uid': meta['uid'], 'generation': meta.get('generation'), 'spec_sha256': sha(canonical(obj['spec']))}


def cm_identity(cm):
    need(cm.get('kind') == 'ConfigMap' and cm['metadata'].get('namespace') == NS
         and not cm['metadata'].get('deletionTimestamp'), 'CONFIGMAP_SHAPE')
    data, binary = cm.get('data', {}), cm.get('binaryData', {})
    need(isinstance(data, dict) and isinstance(binary, dict) and not set(data).intersection(binary), 'CONFIGMAP_DATA_SHAPE')
    need(all(isinstance(k, str) and isinstance(v, str) for k, v in list(data.items()) + list(binary.items())),
         'CONFIGMAP_DATA_TYPES')
    need(len(canonical({'data': data, 'binaryData': binary})) <= 1048576, 'CONFIGMAP_DATA_BOUND')
    return {'uid': cm['metadata']['uid'], 'resource_version': cm['metadata']['resourceVersion'],
            'data_sha256': sha(canonical({'data': data, 'binaryData': binary})),
            'immutable': cm.get('immutable', False)}


def replace_budget(raw):
    """Preserve exact source bytes: one unique scalar token, not YAML reformatting."""
    need(len(raw) == OLD_CONFIG_BYTES and sha(raw) == OLD_CONFIG_SHA, 'ORIGINAL_CONFIG_HASH_REQUIRED')
    lines = raw.splitlines(keepends=True)
    blocks = [i for i, line in enumerate(lines) if re.fullmatch(br'scheduler:[ \t]*(?:#[^\r\n]*)?\r?\n', line)]
    need(len(blocks) == 1, 'SCHEDULER_BLOCK_NOT_UNIQUE')
    start = blocks[0] + 1
    end = next((i for i in range(start, len(lines))
                if lines[i].strip() and not lines[i].startswith((b' ', b'\t', b'#'))), len(lines))
    matches = [(i, re.fullmatch(br'(  recursion_limit:[ \t]*)300([ \t]*(?:#[^\r\n]*)?)(\r?\n|$)', lines[i]))
               for i in range(start, end)]
    matches = [(i, match) for i, match in matches if match]
    need(len(matches) == 1, 'SCHEDULER_RECURSION_SCALAR_NOT_UNIQUE')
    i, match = matches[0]
    offset = sum(len(line) for line in lines[:i]) + len(match.group(1))
    candidate = raw[:offset] + b'10000' + raw[offset + 3:]
    need(len(candidate) == len(raw) + 2 and candidate[:offset] == raw[:offset]
         and candidate[offset + 5:] == raw[offset + 3:], 'CONFIG_BYTE_SCOPE_EXCEEDED')
    return candidate


def config_volume(spec, expected_cm):
    pod = spec['template']['spec']
    containers = pod.get('containers', [])
    need(len(containers) == 1 and containers[0].get('name') == 'gateway', 'GATEWAY_CONTAINER_SHAPE')
    target = PurePosixPath('/app/backend/config.yaml')
    mounts = containers[0].get('volumeMounts', [])
    matches = []
    for mount in mounts:
        base = PurePosixPath(mount['mountPath'])
        need(base.is_absolute() and '..' not in base.parts, 'MOUNT_SHAPE')
        if base == target:
            matches.append(mount)
        else:
            need(base not in target.parents and target not in base.parents, 'CONFIG_SHADOW_MOUNT')
    need(len(matches) == 1 and matches[0].get('subPath') == 'config.yaml'
         and matches[0].get('readOnly') is True and not matches[0].get('subPathExpr'), 'CONFIG_MOUNT_REQUIRED')
    found = [(i, value) for i, value in enumerate(pod.get('volumes', [])) if value['name'] == matches[0]['name']]
    need(len(found) == 1 and found[0][1].get('configMap', {}).get('name') == expected_cm, 'CONFIG_VOLUME_CHANGED')
    return found[0][0]


def build_spec(deployment, cm_name):
    need(identity(deployment) == {'uid': DEPLOYMENT_UID, 'generation': BEFORE_GENERATION,
                                  'spec_sha256': BEFORE_SPEC_SHA}, 'GATEWAY_BASELINE_CHANGED')
    need(deployment['spec'].get('replicas') == 70 and not deployment['spec'].get('paused', False), 'GATEWAY_REPLICAS_OR_PAUSE')
    index = config_volume(deployment['spec'], OLD_CM)
    spec = copy.deepcopy(deployment['spec'])
    spec['template']['spec']['volumes'][index]['configMap']['name'] = cm_name
    restored = copy.deepcopy(spec)
    restored['template']['spec']['volumes'][index]['configMap']['name'] = OLD_CM
    need(restored == deployment['spec'], 'DEPLOYMENT_PATCH_SCOPE_EXCEEDED')
    return spec


def cas_patch(deployment, spec):
    index = config_volume(deployment['spec'], OLD_CM)
    meta = deployment['metadata']
    return [{'op': 'test', 'path': '/metadata/uid', 'value': DEPLOYMENT_UID},
            {'op': 'test', 'path': '/metadata/resourceVersion', 'value': meta['resourceVersion']},
            {'op': 'test', 'path': '/metadata/generation', 'value': BEFORE_GENERATION},
            {'op': 'test', 'path': '/spec', 'value': deployment['spec']},
            {'op': 'replace', 'path': '/spec/template/spec/volumes/%d/configMap/name' % index,
             'value': spec['template']['spec']['volumes'][index]['configMap']['name']}]


def new_configmap(original, name, operation, candidate):
    obj = {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': name, 'namespace': NS,
            'labels': {LABEL: operation}}, 'immutable': True}
    for key in ('data', 'binaryData'):
        if key in original:
            obj[key] = copy.deepcopy(original[key])
    need(obj.get('data', {}).get('config.yaml') == original.get('data', {}).get('config.yaml'), 'ORIGINAL_CONFIG_KEY')
    obj['data']['config.yaml'] = candidate.decode('utf-8')
    restored = copy.deepcopy(obj)
    restored['data']['config.yaml'] = original['data']['config.yaml']
    need(all(restored.get(k, {}) == original.get(k, {}) for k in ('data', 'binaryData')), 'CONFIGMAP_COPY_SCOPE')
    return obj


def source_binding(spec, maps):
    result = {path: {'bytes': length, 'sha256': value} for path, (length, value) in CORE_SOURCES.items()}
    pod = spec['template']['spec']
    mounts = pod['containers'][0].get('volumeMounts', [])
    volumes = {row['name']: row for row in pod.get('volumes', [])}
    for name, paths in OVERLAYS.items():
        cm = maps[name]
        need(cm['metadata']['name'] == name, 'SOURCE_CONFIGMAP_NAME')
        cm_identity(cm)
        for key, path in paths.items():
            need(key in cm.get('data', {}) and key not in cm.get('binaryData', {}), 'SOURCE_CONFIGMAP_KEY')
            raw = cm['data'][key].encode('utf-8')
            need(0 < len(raw) <= 1048576, 'SOURCE_CONFIGMAP_FILE_BOUND')
            target = PurePosixPath(path)
            containing = [row for row in mounts if PurePosixPath(row['mountPath']) == target
                          or PurePosixPath(row['mountPath']) in target.parents]
            need(len(containing) == 1, 'SOURCE_MOUNT_CLOSURE')
            mount = containing[0]
            volume = volumes.get(mount['name'], {})
            need(mount.get('readOnly') is True and not mount.get('subPathExpr')
                 and volume.get('configMap', {}).get('name') == name, 'SOURCE_CONFIGMAP_MOUNT')
            if name == OPENSSL_CM:
                need(mount['mountPath'] == '/etc/deerflow-openssl' and not mount.get('subPath')
                     and (len(raw), sha(raw)) == (189, OPENSSL_SHA), 'OPENSSL_COMPATIBILITY_CHANGED')
            else:
                need(mount['mountPath'] == path and mount.get('subPath') == key, 'SOURCE_SUBPATH_CHANGED')
            items = volume['configMap'].get('items')
            if items is not None:
                need(any(item.get('key') == key and item.get('path') == key for item in items), 'SOURCE_ITEM_MAPPING')
            result[path] = {'bytes': len(raw), 'sha256': sha(raw)}
    need(len(result) == 13, 'SOURCE_BINDING_COUNT')
    return result


def guard_deployment(obj, expected_spec, generation, converged=False):
    need(obj['metadata'].get('uid') == DEPLOYMENT_UID and obj['metadata'].get('generation') == generation
         and not obj['metadata'].get('deletionTimestamp') and obj['spec'] == expected_spec, 'GATEWAY_SPEC_DRIFT')
    if converged:
        need(is_converged(obj, generation), 'GATEWAY_NOT_CONVERGED')


def is_converged(obj, generation):
    status = obj.get('status', {})
    return (status.get('observedGeneration') == generation
            and all(status.get(key, 0) == 70 for key in ('replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas'))
            and status.get('terminatingReplicas', 0) == 0)


def owner(obj, kind):
    found = [row for row in obj['metadata'].get('ownerReferences', [])
             if row.get('kind') == kind and row.get('controller') is True]
    need(len(found) == 1 and isinstance(found[0].get('uid'), str), 'CONTROLLER_OWNER_REQUIRED')
    return found[0]


def pod_rows(items):
    need(isinstance(items, list) and len(items) <= 512, 'POD_LIST_BOUND')
    rows = [pod for pod in items if pod.get('status', {}).get('phase') not in ('Failed', 'Succeeded')]
    need(len({row['metadata']['name'] for row in rows}) == len(rows), 'DUPLICATE_POD_NAMES')
    return sorted(rows, key=lambda row: row['metadata']['name'])


def pod_ips(pod):
    status = pod.get('status', {})
    rows = status.get('podIPs')
    if rows is None:
        rows = [{'ip': status.get('podIP')}]
    need(isinstance(rows, list) and 0 < len(rows) <= 2, 'POD_IP_SET_REQUIRED')
    addresses = []
    for row in rows:
        need(isinstance(row, dict) and set(row) == {'ip'} and isinstance(row['ip'], str), 'POD_IP_SHAPE')
        try:
            address = ipaddress.ip_address(row['ip'])
        except ValueError:
            raise Refused('POD_IP_INVALID')
        need(not address.is_unspecified and not address.is_multicast and not address.is_loopback, 'POD_IP_INVALID')
        addresses.append((address.version, str(address)))
    need(len(set(addresses)) == len(addresses) and len({row[0] for row in addresses}) == len(addresses)
         and isinstance(status.get('podIP'), str)
         and str(ipaddress.ip_address(status['podIP'])) in {row[1] for row in addresses}, 'POD_IP_SET_CHANGED')
    return tuple(sorted(addresses))


DEFAULT_TOLERATIONS = (
    {'key': 'node.kubernetes.io/not-ready', 'operator': 'Exists', 'effect': 'NoExecute', 'tolerationSeconds': 300},
    {'key': 'node.kubernetes.io/unreachable', 'operator': 'Exists', 'effect': 'NoExecute', 'tolerationSeconds': 300},
)


def pod_default_fields(actual, requested):
    """Allow only explicit, inert API/admission defaults outside the template."""
    defaults = {'priority': 0, 'preemptionPolicy': 'PreemptLowerPriority', 'schedulerName': 'default-scheduler',
        'restartPolicy': 'Always', 'dnsPolicy': 'ClusterFirst', 'terminationGracePeriodSeconds': 30,
        'enableServiceLinks': True, 'serviceAccountName': 'default', 'serviceAccount': 'default',
        'securityContext': {}, 'hostNetwork': False, 'hostPID': False, 'hostIPC': False,
        'shareProcessNamespace': False, 'hostUsers': True}
    defaults['serviceAccount'] = requested.get('serviceAccountName', 'default')
    defaults['serviceAccountName'] = requested.get('serviceAccount', 'default')
    extras = set(actual) - set(requested) - {'containers', 'volumes', 'nodeName', 'tolerations'}
    need(all(key in defaults and type(actual[key]) is type(defaults[key]) and actual[key] == defaults[key] for key in extras),
         'POD_UNEXPECTED_SPEC_FIELD')
    need(isinstance(actual.get('nodeName'), str) and re.fullmatch('[a-z0-9][a-z0-9.-]{0,252}', actual['nodeName']),
         'POD_NODE_REQUIRED')
    expected_tolerations, actual_tolerations = requested.get('tolerations', []), actual.get('tolerations', [])
    need(isinstance(expected_tolerations, list) and isinstance(actual_tolerations, list)
         and all(row in actual_tolerations for row in expected_tolerations), 'POD_TOLERATIONS_CHANGED')
    added = [row for row in actual_tolerations if row not in expected_tolerations]
    need(len(added) <= 2 and all(row in DEFAULT_TOLERATIONS for row in added)
         and len({canonical(row) for row in actual_tolerations}) == len(actual_tolerations), 'POD_EXTRA_TOLERATIONS')


def pod_guard(pod, replica_set, deployment, image_id=None):
    meta = pod['metadata']
    ref, rs_ref = owner(pod, 'ReplicaSet'), owner(replica_set, 'Deployment')
    need(ref['name'] == replica_set['metadata']['name'] and ref['uid'] == replica_set['metadata']['uid']
         and rs_ref['uid'] == DEPLOYMENT_UID and rs_ref['name'] == 'gateway', 'POD_OWNER_MISMATCH')
    template = copy.deepcopy(replica_set['spec']['template'])
    template.get('metadata', {}).get('labels', {}).pop('pod-template-hash', None)
    expected = copy.deepcopy(deployment['spec']['template'])
    expected.get('metadata', {}).get('labels', {}).pop('pod-template-hash', None)
    need(template == expected, 'REPLICASET_TEMPLATE_CHANGED')
    need(not meta.get('deletionTimestamp') and pod.get('status', {}).get('phase') == 'Running'
         and not pod['spec'].get('ephemeralContainers'), 'POD_NOT_STABLE_RUNNING')
    requested = deployment['spec']['template']['spec']
    pod_default_fields(pod['spec'], requested)
    for key, value in requested.items():
        if key not in ('containers', 'volumes', 'tolerations'):
            need(pod['spec'].get(key) == value, 'POD_TEMPLATE_FIELD_CHANGED')
    need(len(pod['spec'].get('containers', [])) == len(requested['containers']) == 1, 'POD_CONTAINER_COUNT')
    actual, source = pod['spec']['containers'][0], requested['containers'][0]
    for key, value in source.items():
        if key != 'volumeMounts':
            need(actual.get(key) == value, 'POD_CONTAINER_FIELD_CHANGED')
    need(set(actual) - set(source) <= {'volumeMounts'}, 'POD_CONTAINER_EXTRA_FIELD')
    volumes = {row['name']: row for row in pod['spec'].get('volumes', [])}
    requested_volumes = {row['name']: row for row in requested.get('volumes', [])}
    need(len(volumes) == len(pod['spec'].get('volumes', []))
         and len(requested_volumes) == len(requested.get('volumes', [])), 'POD_DUPLICATE_VOLUME')
    need(all(volumes.get(name) == row for name, row in requested_volumes.items()), 'POD_VOLUME_CHANGED')
    extras = set(volumes) - set(requested_volumes)
    need(len(extras) <= 1 and all(name.startswith('kube-api-access-') and 'projected' in volumes[name] for name in extras),
         'POD_UNEXPECTED_VOLUME')
    actual_mounts = actual.get('volumeMounts', [])
    expected_mounts = source.get('volumeMounts', [])
    need(all(row in actual_mounts for row in expected_mounts), 'POD_MOUNT_CHANGED')
    added = [row for row in actual_mounts if row not in expected_mounts]
    need(len(actual_mounts) == len(expected_mounts) + len(added)
         and len(added) <= 1 and all(row.get('name') in extras and row.get('readOnly') is True
         and row.get('mountPath') == '/var/run/secrets/kubernetes.io/serviceaccount' for row in added), 'POD_EXTRA_MOUNT')
    states = pod.get('status', {}).get('containerStatuses', [])
    need(len(states) == 1 and states[0].get('name') == 'gateway' and states[0].get('ready') is True
         and states[0].get('restartCount') == 0 and states[0].get('state', {}).get('running')
         and any(row.get('type') == 'Ready' and row.get('status') == 'True' for row in pod['status'].get('conditions', [])),
         'POD_NOT_READY_OR_RESTARTED')
    state = states[0]
    need(isinstance(state.get('containerID'), str) and isinstance(state.get('imageID'), str), 'POD_RUNTIME_ID_REQUIRED')
    need(image_id is None or state['imageID'] == image_id, 'POD_IMAGE_ID_CHANGED')
    return {'name': meta['name'], 'uid': meta['uid'], 'spec_sha256': sha(canonical(pod['spec'])),
            'container_id_sha256': sha(state['containerID'].encode()), 'image_id_sha256': sha(state['imageID'].encode()),
            'node': pod['spec']['nodeName'], 'restart_count': state['restartCount'],
            'running_since': state['state']['running'].get('startedAt'),
            'pod_ips_sha256': sha(canonical(pod_ips(pod)))}


def port_name(value):
    need(value is None or isinstance(value, str), 'SERVICE_PORT_NAME_TYPE')
    return value or None


def resolved_service_ports(service, pods):
    ports = service.get('spec', {}).get('ports')
    need(isinstance(ports, list) and 0 < len(ports) <= 16, 'SERVICE_PORT_SET_REQUIRED')
    result = set()
    for row in ports:
        need(type(row.get('port')) is int and 1 <= row['port'] <= 65535, 'SERVICE_PORT_INVALID')
        protocol, name = row.get('protocol', 'TCP'), port_name(row.get('name'))
        need(protocol in ('TCP', 'UDP', 'SCTP'), 'SERVICE_PORT_IDENTITY')
        target = row.get('targetPort', row['port'])
        resolved = set()
        for pod in pods:
            declared = [value for container in pod['spec'].get('containers', []) for value in container.get('ports', [])]
            if type(target) is int:
                need(1 <= target <= 65535, 'TARGET_PORT_INVALID')
                # A numeric targetPort is legal without containerPort metadata;
                # when declared, its protocol must agree. The numeric value
                # remains the exact route target even when it is undeclared.
                matching = [item for item in declared if item.get('containerPort') == target]
                need(not matching or any(item.get('protocol', 'TCP') == protocol for item in matching), 'CONTAINER_PORT_PROTOCOL_MISMATCH')
                port = target
            else:
                need(isinstance(target, str) and target, 'TARGET_PORT_INVALID')
                matching = [item for item in declared if item.get('name') == target and item.get('protocol', 'TCP') == protocol]
                need(len(matching) == 1 and type(matching[0].get('containerPort')) is int
                     and 1 <= matching[0]['containerPort'] <= 65535, 'NAMED_CONTAINER_PORT_NOT_UNIQUE')
                port = matching[0]['containerPort']
            resolved.add(port)
        need(len(resolved) == 1, 'SERVICE_TARGET_PORT_VARIANCE')
        identity_row = (name, protocol, next(iter(resolved)), row.get('appProtocol'))
        need(identity_row not in result, 'DUPLICATE_SERVICE_PORT')
        result.add(identity_row)
    return result


def endpoints(slices, service, pods):
    need(isinstance(slices.get('items'), list) and 0 < len(slices['items']) <= 128, 'ENDPOINT_SLICE_BOUND')
    pod_set = {(pod['metadata']['name'], pod['metadata']['uid']): dict(pod_ips(pod)) for pod in pods}
    need(len(pod_set) == len(pods) == 70, 'ENDPOINT_VERIFIED_POD_COUNT')
    families = service.get('spec', {}).get('ipFamilies')
    if families is None:
        cluster_ip = service.get('spec', {}).get('clusterIP')
        need(isinstance(cluster_ip, str) and cluster_ip != 'None', 'SERVICE_ADDRESS_FAMILY_REQUIRED')
        families = ['IPv%d' % ipaddress.ip_address(cluster_ip).version]
    need(isinstance(families, list) and 0 < len(families) <= 2 and len(set(families)) == len(families)
         and all(value in ('IPv4', 'IPv6') for value in families), 'SERVICE_ADDRESS_FAMILY_INVALID')
    versions = {int(value[-1]) for value in families}
    expected = set()
    for target, addresses in pod_set.items():
        need(versions.issubset(addresses), 'POD_SERVICE_FAMILY_MISSING')
        expected.update((target[0], target[1], version, addresses[version]) for version in versions)
    expected_ports = resolved_service_ports(service, pods)
    found, addresses_seen, slice_names = set(), set(), set()
    for item in slices['items']:
        slice_name = item['metadata'].get('name')
        need(isinstance(slice_name, str) and slice_name not in slice_names, 'ENDPOINT_SLICE_DUPLICATE')
        slice_names.add(slice_name)
        need(item['metadata'].get('namespace', NS) == NS
             and item['metadata'].get('labels', {}).get('kubernetes.io/service-name') == 'gateway'
             and any(row.get('kind') == 'Service' and row.get('name') == 'gateway' and row.get('uid') == service['metadata']['uid']
                 for row in item['metadata'].get('ownerReferences', [])), 'ENDPOINT_SERVICE_OWNER')
        family = item.get('addressType')
        need(family in families, 'ENDPOINT_ADDRESS_TYPE_CHANGED')
        port_rows = item.get('ports')
        need(isinstance(port_rows, list) and len(port_rows) == len(expected_ports), 'ENDPOINT_PORT_SET_CHANGED')
        need(all(isinstance(row, dict) and type(row.get('port')) is int
             and 1 <= row['port'] <= 65535 and row.get('protocol', 'TCP') in ('TCP', 'UDP', 'SCTP')
             and (row.get('name') is None or isinstance(row['name'], str))
             and (row.get('appProtocol') is None or isinstance(row['appProtocol'], str)) for row in port_rows),
             'ENDPOINT_PORT_SHAPE')
        actual_ports = {(port_name(row.get('name')), row.get('protocol', 'TCP'), row.get('port'), row.get('appProtocol')) for row in port_rows}
        need(len(actual_ports) == len(port_rows) and actual_ports == expected_ports, 'ENDPOINT_PORT_SET_CHANGED')
        for row in item.get('endpoints', []):
            condition, target = row.get('conditions', {}), row.get('targetRef', {})
            need(condition.get('ready') is True and condition.get('terminating', False) is False
                 and target.get('kind') == 'Pod' and target.get('namespace', NS) == NS
                 and isinstance(target.get('name'), str) and isinstance(target.get('uid'), str), 'ENDPOINT_NOT_READY')
            key = target['name'], target['uid']
            need(key in pod_set and isinstance(row.get('addresses'), list) and len(row['addresses']) == 1,
                 'ENDPOINT_ADDRESS_SET_CHANGED')
            try:
                address = ipaddress.ip_address(row['addresses'][0])
            except (TypeError, ValueError):
                raise Refused('ENDPOINT_ADDRESS_INVALID')
            need('IPv%d' % address.version == family, 'ENDPOINT_ADDRESS_FAMILY_MISMATCH')
            value = (key[0], key[1], address.version, str(address))
            address_key = address.version, str(address)
            need(value in expected and value not in found and address_key not in addresses_seen, 'ENDPOINT_ADDRESS_MISMATCH_OR_DUPLICATE')
            found.add(value)
            addresses_seen.add(address_key)
    need(found == expected, 'ENDPOINT_ADDRESS_CLOSURE_MISMATCH')
    return set(pod_set)


class Operator:
    def __init__(self, child, readonly=False):
        self.child, self.readonly = child, readonly
        self.started, self.commands, self.sequence = time.monotonic(), 0, 0
        self.operation = uuid.uuid4().hex
        self.plan = self.before = self.expected = self.manifest = None
        self.known_pods, self.known_rs = set(), set()
        self.report = {'schema': 'scheduler-budget-activation.v1', 'tool_revision': 'r2', 'operation': self.operation,
            'status': 'INCOMPLETE', 'once_claimed': False, 'automatic_retry': False, 'automatic_rollback': False,
            'patch_request_count': 0, 'patch_may_have_applied': False, 'configmap_create_requests': 0,
            'secret_api_reads': 0, 'credentials_exported': False, 'raw_configuration_exported': False,
            'raw_errors_exported': False, 'explicit_database_queries': 0, 'provider_requests': 0, 'work_messages_sent': 0,
            'other_deployments_modified': False, 'existing_runs_replayed': False, 'existing_run_budgets_modified': False,
            'future_scheduled_runs_budget': 10000, 'global_max_recursion_limit': 10000,
            'all_existing_gateway_spec_fields_except_configmap_name_preserved': True,
            'active_tasks_may_be_interrupted': not readonly, 'active_task_safe_shutdown_proven': False,
            'normal_application_startup_database_and_redis_access_may_occur': not readonly,
            'existing_background_schedules_may_execute_normally': True, 'business_acceptance_pending': True,
            'ca_secret_contents_read': False, 'ca_secret_references_preserved': True,
            'readonly': readonly, 'persistent_files_modified': False}

    def journal(self, phase):
        self.report['last_step'] = phase
        if self.report['once_claimed'] and not self.readonly:
            private_write(CONTROL / ('JOURNAL-%04d.json' % self.sequence), canonical(self.report))
            self.sequence += 1

    def command(self, args, body=None, timeout=30, limit=4194304):
        need(isinstance(args, list) and all(isinstance(item, str) for item in args), 'COMMAND_ARGUMENT_TYPES')
        need(args[:1] != ['get'] or body is None, 'READ_COMMAND_BODY_REFUSED')
        allowed = args in [
            ['get', 'deployment', 'gateway', '-o', 'json'], ['get', 'service', 'gateway', '-o', 'json'],
            ['get', 'pods', '-l', 'app=gateway', '-o', 'json'],
            ['get', 'endpointslices', '-l', 'kubernetes.io/service-name=gateway', '-o', 'json']]
        if args[:2] == ['get', 'configmap']:
            allowed = len(args) == 5 and args[3:] == ['-o', 'json'] and args[2] in set(OVERLAYS) | {OLD_CM} | ({self.plan['new_configmap']} if self.plan else set())
        if args[:2] == ['get', 'pod']:
            allowed = len(args) == 5 and args[2] in self.known_pods and args[3:] == ['-o', 'json']
        if args[:2] == ['get', 'replicaset']:
            allowed = len(args) == 5 and args[2] in self.known_rs and args[3:] == ['-o', 'json']
        if args[:1] == ['exec']:
            allowed = (len(args) == 12 and args[1] == '-i' and args[2] in self.known_pods
                       and args[3:] == ['-c', 'gateway', '--', '/app/backend/.venv/bin/python', '-I', '-B', '-u', '-c', self.child]
                       and body is not None and len(body) <= 262144)
        if args[:1] == ['create']:
            need(not self.readonly and self.report['once_claimed'] and self.report['configmap_create_requests'] == 1,
                 'CREATE_NOT_AUTHORIZED')
            allowed = args == ['create', '-f', '-', '-o', 'json'] and body == canonical(self.manifest)
        if args[:1] == ['patch']:
            need(not self.readonly and self.report['once_claimed'] and self.report['patch_request_count'] == 1,
                 'PATCH_NOT_AUTHORIZED')
            allowed = (args == ['patch', 'deployment', 'gateway', '--type=json', '--patch-file', str(CONTROL / 'PATCH.json'), '-o', 'json']
                       and body is None and private_read(CONTROL / 'PATCH.json') == canonical(cas_patch(self.before, self.expected)))
        need(allowed, 'COMMAND_SCOPE')
        self.commands += 1
        remaining = TOTAL_SECONDS - (time.monotonic() - self.started)
        need(self.commands <= 2600 and remaining > 0, 'OPERATOR_BUDGET')
        timeout = min(timeout, remaining)
        prefix = list(KUBE)
        prefix[2] = '--request-timeout=%ds' % max(20, min(120, int(timeout) - 2))
        with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            if body is not None:
                need(len(body) <= 4194304, 'COMMAND_INPUT_BOUND')
                source.write(body)
                source.seek(0)
            process = subprocess.Popen(prefix + args, stdin=source if body is not None else subprocess.DEVNULL,
                stdout=output, stderr=errors, env={'PATH': SAFE_PATH, 'HOME': '/root', 'LANG': 'C.UTF-8'},
                start_new_session=os.name == 'posix')
            until = time.monotonic() + timeout
            try:
                while process.poll() is None:
                    need(os.fstat(output.fileno()).st_size <= limit and os.fstat(errors.fileno()).st_size <= 131072,
                         'COMMAND_OUTPUT_BOUND')
                    need(time.monotonic() < until, 'COMMAND_TIMEOUT')
                    time.sleep(0.05)
                need(os.fstat(output.fileno()).st_size <= limit and os.fstat(errors.fileno()).st_size <= 131072,
                     'COMMAND_OUTPUT_BOUND')
                need(process.returncode in ((0, 1) if args[:1] == ['exec'] else (0,)), 'KUBECTL_COMMAND_FAILED')
                output.seek(0)
                return json.loads(output.read(limit + 1))
            finally:
                if process.poll() is None:
                    if os.name == 'posix':
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                    process.wait(timeout=5)

    def get(self, kind, name):
        return self.command(['get', kind, name, '-o', 'json'])

    def qualification(self, pod, candidate=False):
        payload = {'mode': 'qualify' if candidate else 'verify', 'sources': self.plan['sources'],
            'config': {'bytes': OLD_CONFIG_BYTES if candidate else self.plan['new_config_bytes'],
                       'sha256': OLD_CONFIG_SHA if candidate else self.plan['new_config_sha256'],
                       'scheduler_recursion_limit': 300 if candidate else 10000}}
        if candidate:
            payload['candidate'] = {'bytes': self.plan['new_config_bytes'], 'sha256': self.plan['new_config_sha256'],
                'scheduler_recursion_limit': 10000, 'base64': base64.b64encode(self.manifest['data']['config.yaml'].encode('utf-8')).decode('ascii')}
        result = self.command(['exec', '-i', pod, '-c', 'gateway', '--', '/app/backend/.venv/bin/python', '-I', '-B', '-u', '-c', self.child],
                              body=canonical(payload), timeout=40, limit=65536)
        if result.get('status') != 'SCHEDULER_BUDGET_READONLY_QUALIFIED':
            self.report['qualification_error_class'] = result.get('error_class') if re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}', str(result.get('error_class', ''))) else 'UNKNOWN'
            self.report['qualification_error_code'] = result.get('error_code') if re.fullmatch('[A-Z][A-Z0-9_]{0,95}', str(result.get('error_code', ''))) else 'CHILD_QUALIFICATION_FAILED'
        need(result.get('status') == 'SCHEDULER_BUDGET_READONLY_QUALIFIED' and result.get('stable_readback') is True,
             'READONLY_CONFIG_QUALIFICATION_FAILED')
        return result

    def snapshot_maps(self):
        return {name: self.get('configmap', name) for name in [OLD_CM] + sorted(OVERLAYS)}

    def check_maps(self):
        current = self.snapshot_maps()
        need({name: cm_identity(obj) for name, obj in current.items()} == self.plan['old_configmaps'],
             'ORIGINAL_CONFIGMAP_CLOSURE_CHANGED')
        return current

    def preflight(self):
        self.journal('READ_GATEWAY_414_AND_FIXED_CONFIGMAPS')
        before = self.get('deployment', 'gateway')
        need(identity(before) == {'uid': DEPLOYMENT_UID, 'generation': BEFORE_GENERATION, 'spec_sha256': BEFORE_SPEC_SHA},
             'GATEWAY_BASELINE_CHANGED')
        guard_deployment(before, before['spec'], BEFORE_GENERATION, True)
        maps = self.snapshot_maps()
        original = maps[OLD_CM]
        raw = original.get('data', {}).get('config.yaml', '').encode('utf-8')
        candidate = replace_budget(raw)
        name = 'deer-flow-scheduler-budget-10000-' + sha(candidate)[:16]
        self.before, self.expected = before, build_spec(before, name)
        self.manifest = new_configmap(original, name, self.operation, candidate)
        sources = source_binding(before['spec'], {key: maps[key] for key in OVERLAYS})
        service = self.get('service', 'gateway')
        need(service['metadata'].get('namespace') == NS and not service['metadata'].get('deletionTimestamp')
             and service['spec'].get('selector') == before['spec']['selector'].get('matchLabels'), 'GATEWAY_SERVICE_SELECTOR')
        self.plan = {'schema': 'scheduler-budget-private-plan.v1', 'operation': self.operation,
            'before': identity(before), 'after_spec_sha256': sha(canonical(self.expected)), 'new_configmap': name,
            'new_config_bytes': len(candidate), 'new_config_sha256': sha(candidate), 'sources': sources,
            'old_configmaps': {key: cm_identity(value) for key, value in maps.items()},
            'service': {'uid': service['metadata']['uid'], 'spec_sha256': sha(canonical(service['spec']))},
            'before_file_sha256': sha(canonical(before)), 'candidate_spec_sha256': sha(canonical(self.expected)),
            'candidate_configmap_sha256': sha(canonical(self.manifest)), 'child_sha256': sha(self.child.encode('utf-8'))}
        rows = pod_rows(self.command(['get', 'pods', '-l', 'app=gateway', '-o', 'json'])['items'])
        need(len(rows) == 70, 'BASELINE_ACTIVE_POD_COUNT')
        self.known_pods.update(row['metadata']['name'] for row in rows)
        sample = rows[0]
        rs_name = owner(sample, 'ReplicaSet')['name']
        self.known_rs.add(rs_name)
        rs = self.get('replicaset', rs_name)
        stamp = pod_guard(sample, rs, before)
        self.plan['image_id'] = sample['status']['containerStatuses'][0]['imageID']
        qualification = self.qualification(sample['metadata']['name'], True)
        need(pod_guard(self.get('pod', sample['metadata']['name']), rs, before) == stamp, 'PREFLIGHT_SAMPLE_CHANGED')
        self.check_maps()
        fresh = self.get('deployment', 'gateway')
        guard_deployment(fresh, before['spec'], BEFORE_GENERATION, True)
        self.before = fresh
        self.plan['before_file_sha256'] = sha(canonical(fresh))
        self.report.update(before=identity(fresh), sample=stamp, qualification={key: qualification[key]
            for key in ('config_sha256', 'candidate_sha256', 'scheduler_recursion_limit', 'candidate_scheduler_recursion_limit',
                        'max_recursion_limit', 'source_files_verified', 'versions', 'semantic_change_only_scheduler_recursion_limit')},
            candidate_config_sha256=sha(candidate), candidate_spec_sha256=sha(canonical(self.expected)))
        return maps

    def claim(self, maps):
        safe(CONTROL)
        need(not CONTROL.exists(), 'ONCE_DIRECTORY_EXISTS_REVIEW_REQUIRED')
        os.mkdir(str(CONTROL), 0o700)
        self.report.update(once_claimed=True, persistent_files_modified=True)
        private_write(CONTROL / 'BEFORE.json', canonical(self.before))
        private_write(CONTROL / 'ORIGINAL-CONFIGMAPS.json', canonical(maps))
        private_write(CONTROL / 'CANDIDATE-SPEC.json', canonical(self.expected))
        private_write(CONTROL / 'CANDIDATE-CONFIGMAP.json', canonical(self.manifest))
        private_write(CONTROL / 'PLAN.json', canonical(self.plan))
        self.report['plan_sha256'] = sha(canonical(self.plan))
        self.journal('CLAIMED_PRIVATE_BACKUP_COMPLETE')

    def cutover(self):
        self.report['configmap_create_requests'] = 1
        self.journal('CREATE_ONLY_NEW_IMMUTABLE_CONFIGMAP')
        created = self.command(['create', '-f', '-', '-o', 'json'], body=canonical(self.manifest))
        need(all(created.get(key) == self.manifest.get(key) for key in ('data', 'binaryData', 'immutable'))
             and created['metadata']['name'] == self.plan['new_configmap']
             and created['metadata'].get('labels', {}).get(LABEL) == self.operation, 'CREATED_CONFIGMAP_MISMATCH')
        private_write(CONTROL / 'CREATED-CONFIGMAP-IDENTITY.json', canonical(cm_identity(created)))
        self.journal('PRE_PATCH_REVALIDATION')
        self.check_maps()
        fresh = self.get('deployment', 'gateway')
        guard_deployment(fresh, self.before['spec'], BEFORE_GENERATION, True)
        self.before = fresh
        private_write(CONTROL / 'PATCH.json', canonical(cas_patch(fresh, self.expected)))
        self.report.update(patch_request_count=1, patch_may_have_applied=True)
        self.journal('SINGLE_FULL_SPEC_CAS_PATCH_REQUEST')
        after = self.command(['patch', 'deployment', 'gateway', '--type=json', '--patch-file', str(CONTROL / 'PATCH.json'), '-o', 'json'])
        guard_deployment(after, self.expected, BEFORE_GENERATION + 1)
        self.report['patch_acknowledged'] = True
        self.journal('WAIT_EXISTING_STRATEGY_GATEWAY_415_ROLLOUT')

    def rollout(self):
        until, last = time.monotonic() + ROLLOUT_SECONDS, 0
        while True:
            current = self.get('deployment', 'gateway')
            guard_deployment(current, self.expected, BEFORE_GENERATION + 1)
            status = current.get('status', {})
            if time.monotonic() - last >= 30:
                print(json.dumps({'phase': 'WAIT_GATEWAY_415_ROLLOUT', 'desired': 70,
                    'observed_generation': status.get('observedGeneration', 0),
                    'updated': status.get('updatedReplicas', 0), 'ready': status.get('readyReplicas', 0),
                    'available': status.get('availableReplicas', 0)}), flush=True)
                last = time.monotonic()
            if is_converged(current, BEFORE_GENERATION + 1):
                return current
            need(not any(row.get('type') == 'Progressing' and row.get('status') == 'False'
                         and row.get('reason') == 'ProgressDeadlineExceeded' for row in status.get('conditions', [])),
                 'ROLLOUT_PROGRESS_DEADLINE')
            need(time.monotonic() < until, 'ROLLOUT_WAIT_TIMEOUT')
            time.sleep(10)

    def load_plan(self):
        directory = safe(CONTROL).stat()
        need(stat.S_ISDIR(directory.st_mode) and directory.st_uid == 0 and stat.S_IMODE(directory.st_mode) == 0o700,
             'CONTROL_DIRECTORY_OWNER_MODE')
        raw = private_read(CONTROL / 'PLAN.json')
        self.plan = json.loads(raw)
        need(self.plan.get('schema') == 'scheduler-budget-private-plan.v1'
             and re.fullmatch('[0-9a-f]{32}', self.plan.get('operation', ''))
             and self.plan['before'] == {'uid': DEPLOYMENT_UID, 'generation': BEFORE_GENERATION, 'spec_sha256': BEFORE_SPEC_SHA}
             and self.plan['child_sha256'] == sha(self.child.encode('utf-8')), 'PRIVATE_PLAN_BINDING')
        self.operation = self.plan['operation']
        self.report.update(operation=self.operation, plan_sha256=sha(raw), original_receipts_modified=False,
                           repeat_readonly_check_safe=True)
        self.before = json.loads(private_read(CONTROL / 'BEFORE.json'))
        self.expected = json.loads(private_read(CONTROL / 'CANDIDATE-SPEC.json'))
        self.manifest = json.loads(private_read(CONTROL / 'CANDIDATE-CONFIGMAP.json'))
        maps = json.loads(private_read(CONTROL / 'ORIGINAL-CONFIGMAPS.json'))
        need(sha(canonical(self.before)) == self.plan['before_file_sha256']
             and sha(canonical(self.expected)) == self.plan['candidate_spec_sha256']
             and sha(canonical(self.manifest)) == self.plan['candidate_configmap_sha256']
             and build_spec(self.before, self.plan['new_configmap']) == self.expected
             and {name: cm_identity(value) for name, value in maps.items()} == self.plan['old_configmaps'], 'PRIVATE_BACKUP_BINDING')
        rebuilt = replace_budget(maps[OLD_CM]['data']['config.yaml'].encode('utf-8'))
        need(new_configmap(maps[OLD_CM], self.plan['new_configmap'], self.operation, rebuilt) == self.manifest
             and source_binding(self.before['spec'], {key: maps[key] for key in OVERLAYS}) == self.plan['sources'], 'PRIVATE_CANDIDATE_BINDING')
        journal_paths = sorted(CONTROL.glob('JOURNAL-*.json'))
        need(0 < len(journal_paths) <= 32 and all(re.fullmatch(r'JOURNAL-\d{4}\.json', p.name) for p in journal_paths), 'JOURNAL_SET')
        records = [json.loads(private_read(path, 262144)) for path in journal_paths]
        need(all(row.get('operation') == self.operation and row.get('plan_sha256') == sha(raw) for row in records), 'JOURNAL_PLAN_BINDING')
        need(any(row.get('patch_request_count') == 1 and row.get('patch_may_have_applied') is True
                 and row.get('last_step') == 'SINGLE_FULL_SPEC_CAS_PATCH_REQUEST' for row in records), 'NO_RECORDED_PATCH_REQUEST')
        self.report['accepted_original_patch_journal'] = {
            'operation': self.operation, 'plan_sha256': sha(raw),
            'journal_count': len(records), 'last_journal_sha256': sha(canonical(records[-1])),
            'patch_request_recorded': True}
        patch = json.loads(private_read(CONTROL / 'PATCH.json'))
        need(patch[:1] == [{'op': 'test', 'path': '/metadata/uid', 'value': DEPLOYMENT_UID}]
             and len(patch) == 5 and patch[2] == {'op': 'test', 'path': '/metadata/generation', 'value': BEFORE_GENERATION}
             and set(patch[1]) == {'op', 'path', 'value'} and patch[1]['op'] == 'test'
             and patch[1]['path'] == '/metadata/resourceVersion' and isinstance(patch[1]['value'], str)
             and patch[3] == {'op': 'test', 'path': '/spec', 'value': self.before['spec']}
             and patch[4] == cas_patch(self.before, self.expected)[4], 'PRIVATE_PATCH_SCOPE')

    def verify(self, deployment):
        self.journal('READONLY_VERIFY_SEVENTY_GATEWAY_PODS')
        guard_deployment(deployment, self.expected, BEFORE_GENERATION + 1, True)
        self.check_maps()
        new_cm = self.get('configmap', self.plan['new_configmap'])
        expected_identity = json.loads(private_read(CONTROL / 'CREATED-CONFIGMAP-IDENTITY.json'))
        need(cm_identity(new_cm) == expected_identity and all(new_cm.get(key) == self.manifest.get(key)
             for key in ('data', 'binaryData', 'immutable')), 'NEW_CONFIGMAP_CHANGED')
        rows = pod_rows(self.command(['get', 'pods', '-l', 'app=gateway', '-o', 'json'])['items'])
        need(len(rows) == 70, 'FINAL_ACTIVE_POD_COUNT')
        self.known_pods.update(row['metadata']['name'] for row in rows)
        cache, accepted = {}, []
        for index, pod in enumerate(rows):
            name = pod['metadata']['name']
            rs_name = owner(pod, 'ReplicaSet')['name']
            self.known_rs.add(rs_name)
            if rs_name not in cache:
                cache[rs_name] = self.get('replicaset', rs_name)
            stamp = pod_guard(pod, cache[rs_name], deployment, self.plan['image_id'])
            self.qualification(name)
            need(pod_guard(self.get('pod', name), cache[rs_name], deployment, self.plan['image_id']) == stamp,
                 'POD_CHANGED_DURING_QUALIFICATION')
            accepted.append(stamp)
            if index % 10 == 9:
                print(json.dumps({'phase': 'READONLY_VERIFY_GATEWAY_415', 'verified_pods': index + 1, 'expected': 70}), flush=True)
        need(len(cache) == 1, 'MULTIPLE_FINAL_REPLICASETS')
        service = self.get('service', 'gateway')
        need({'uid': service['metadata']['uid'], 'spec_sha256': sha(canonical(service['spec']))} == self.plan['service'], 'SERVICE_CHANGED')
        expected_endpoints = {(row['name'], row['uid']) for row in accepted}
        need(endpoints(self.command(['get', 'endpointslices', '-l', 'kubernetes.io/service-name=gateway', '-o', 'json']),
                       service, rows) == expected_endpoints, 'SERVICE_ENDPOINT_SET_MISMATCH')
        final_rows = pod_rows(self.command(['get', 'pods', '-l', 'app=gateway', '-o', 'json'])['items'])
        need({(row['metadata']['name'], row['metadata']['uid']) for row in final_rows} == expected_endpoints,
             'FINAL_FLEET_CHANGED')
        for pod in final_rows:
            need(pod_guard(pod, cache[owner(pod, 'ReplicaSet')['name']], deployment, self.plan['image_id'])
                 == next(row for row in accepted if row['uid'] == pod['metadata']['uid']), 'FINAL_RUNTIME_CHANGED')
        for name, cached in cache.items():
            fresh_rs = self.get('replicaset', name)
            need(fresh_rs['metadata']['uid'] == cached['metadata']['uid'] and fresh_rs['spec'] == cached['spec'],
                 'FINAL_REPLICASET_CHANGED')
        final = self.get('deployment', 'gateway')
        guard_deployment(final, self.expected, BEFORE_GENERATION + 1, True)
        self.check_maps()
        self.report.update(status='SCHEDULER_BUDGET_10000_DEPLOYED_RUNTIME_ACCEPTED', after=identity(final),
            all_gateway_pods_verified=True, verified_pods=70, verified_source_files_per_pod=13,
            service_backends_verified=True, verified_fleet_sha256=sha(canonical(accepted)),
            original_configmaps_unchanged=True, loaded_application_modules_verified=False,
            business_acceptance_pending=True)
        self.journal('RUNTIME_ACCEPTED_FUTURE_SCHEDULED_RUN_ACCEPTANCE_PENDING')

    def run(self):
        if self.readonly:
            self.load_plan()
            current = self.get('deployment', 'gateway')
            if identity(current) == self.plan['before'] and current['spec'] == self.before['spec']:
                self.report.update(status='SCHEDULER_BUDGET_PATCH_NOT_OBSERVED_REVIEW_REQUIRED',
                                   current=identity(current), error_code='RECORDED_PATCH_NOT_OBSERVED_NO_RETRY')
                return
            guard_deployment(current, self.expected, BEFORE_GENERATION + 1)
            self.report['current'] = dict(identity(current), **{key: current.get('status', {}).get(key, 0)
                for key in ('observedGeneration', 'replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas', 'terminatingReplicas')})
            if not is_converged(current, BEFORE_GENERATION + 1):
                self.report['status'] = 'GATEWAY_415_ROLLOUT_IN_PROGRESS_NO_CHANGE'
                return
            self.verify(current)
        else:
            maps = self.preflight()
            self.claim(maps)
            self.cutover()
            self.verify(self.rollout())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--activate-scheduler-budget-once', action='store_true')
    group.add_argument('--verify-readonly', action='store_true')
    options = parser.parse_args(argv)
    operator = Operator('', readonly=options.verify_readonly)
    try:
        need(sys.version_info >= (3, 7), 'PYTHON_37_REQUIRED')
        need(os.name == 'posix' and os.uname().nodename.split('.')[0] == 'datax-01', 'DATAX01_REQUIRED')
        need(os.geteuid() == 0, 'ROOT_REQUIRED')
        os.umask(0o077)
        operator.child = load_child()
        operator.run()
    except KeyboardInterrupt:
        operator.report.update(error_class='KeyboardInterrupt', error_code='OPERATOR_INTERRUPTED_ROLLOUT_MAY_CONTINUE',
                               verify_readonly_available=operator.report['patch_may_have_applied'])
    except Exception as exc:
        operator.report.update(error_class=type(exc).__name__, error_code=str(exc) if isinstance(exc, Refused) else 'LOCAL_FAILURE')
    finally:
        operator.report.update(commands=operator.commands, elapsed_seconds=round(time.monotonic() - operator.started, 3),
                               observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
        if operator.report['once_claimed'] and not operator.readonly:
            try:
                operator.journal('FINAL_OPERATOR_RESULT')
                report_raw = canonical(operator.report)
                private_write(CONTROL / 'REPORT.json', report_raw)
                operator.report['report_path'] = str(CONTROL / 'REPORT.json')
                operator.report['report_bytes'] = len(report_raw)
                operator.report['report_sha256'] = sha(report_raw)
            except Exception as exc:
                operator.report['report_write_error_class'] = type(exc).__name__
        print(json.dumps(operator.report, sort_keys=True), flush=True)
    return 0 if operator.report['status'] in ('SCHEDULER_BUDGET_10000_DEPLOYED_RUNTIME_ACCEPTED', 'GATEWAY_415_ROLLOUT_IN_PROGRESS_NO_CHANGE') else 1


if __name__ == '__main__':
    sys.exit(main())
