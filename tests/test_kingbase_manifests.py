import pathlib
import subprocess
import yaml
import unittest

ROOT = pathlib.Path(__file__).parents[1]
BASE = ROOT / "k8s/local-pvc-kingbase"


class ManifestTests(unittest.TestCase):
    def test_architecture_and_storage_isolation(self):
        for arch, suffix in [("amd64", "x86"), ("arm64", "arm")]:
            output = subprocess.run(
                ["kubectl", "kustomize", str(BASE / "overlays" / arch)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            docs = list(yaml.safe_load_all(output))
            sts = next(x for x in docs if x["kind"] == "StatefulSet")
            pod = sts["spec"]["template"]["spec"]
            self.assertEqual(pod["nodeSelector"]["kubernetes.io/arch"], arch)
            self.assertEqual(
                pod["containers"][0]["image"],
                f"kingbase_v009r001c010b0004_single_{suffix}:v1",
            )
            self.assertEqual(pod["containers"][0]["imagePullPolicy"], "Never")
            self.assertFalse(any(x["metadata"]["name"] == "postgres" for x in docs))
            self.assertEqual(pod["securityContext"]["runAsUser"], 1000)
            self.assertTrue(sts["spec"]["volumeClaimTemplates"])
            service = next(x for x in docs if x["kind"] == "Service")
            self.assertEqual(service["spec"]["type"], "ClusterIP")

    def test_operations_match_sources_and_backup_is_suspended(self):
        for arch in ("amd64", "arm64"):
            for action in ("backup", "restore"):
                doc = yaml.safe_load(
                    (BASE / "operations" / arch / f"{action}.yaml").read_text()
                )
                if action == "backup":
                    self.assertTrue(doc["spec"]["suspend"])
                    job = doc["spec"]["jobTemplate"]["spec"]
                else:
                    job = doc["spec"]
                command = job["template"]["spec"]["containers"][0]["command"]
                self.assertEqual(
                    command[-1], (BASE / "operations" / f"{action}.sh").read_text()
                )
                self.assertEqual(job["backoffLimit"], 0)


if __name__ == "__main__":
    unittest.main()
