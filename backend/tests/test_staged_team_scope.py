from deerflow.config import staged_agent_scope as agent_scope
import ast
import copy
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

class ScopeTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.team = self.root / 'team' / 'policy'
        self.team.mkdir(parents=True)
        (self.team / 'config.yaml').write_text('name: policy')
        self.stage = SimpleNamespace(enabled=True, team_agents=['policy'])
        self.app = SimpleNamespace(agent_storage=SimpleNamespace(backend='file'))
        self.personal = False
        self.resolved = self.team
        self.user_ids = []
        ac = ModuleType('deerflow.config.agents_config')
        ac.validate_agent_name = lambda name: name

        def resolve(name, *, user_id):
            self.user_ids.append(user_id)
            return self.resolved
        ac.resolve_agent_dir = resolve
        paths = ModuleType('deerflow.config.paths')
        paths.get_paths = lambda: SimpleNamespace(team_agent_dir=lambda name: self.root / 'team' / name, agent_dir=lambda name: self.root / 'legacy' / name)
        store = ModuleType('deerflow.persistence.agents')
        store.get_agent_store = lambda: SimpleNamespace(exists=lambda name, user_id: self.personal)
        self.addCleanup(patch.stopall)
        patch.dict(sys.modules, {ac.__name__: ac, paths.__name__: paths, store.__name__: store}).start()

    def resolve(self, name='policy', user='browser-user'):
        return agent_scope.resolve_staged_team_agent(self.stage, name, user_id=user, app_config=self.app)

    def test_two_browser_users_and_oa_owner_use_same_team(self):
        for user in ['browser-a', 'browser-b', 'oa-owner']:
            self.assertEqual(self.resolve(user=user), 'policy')
        self.assertEqual(self.user_ids, ['browser-a', 'browser-b', 'oa-owner'])

    def test_personal_same_name_excluded(self):
        self.resolved = self.root / 'personal' / 'policy'
        self.assertIsNone(self.resolve())

    def test_database_personal_same_name_excluded(self):
        self.app.agent_storage.backend = 'db'
        self.personal = True
        self.assertIsNone(self.resolve())
        self.personal = False
        self.assertEqual(self.resolve(), 'policy')

    def test_non_target_default_off_and_missing_team(self):
        self.assertIsNone(self.resolve('other'))
        self.stage.enabled = False
        self.assertIsNone(self.resolve())
        self.stage.enabled = True
        (self.team / 'config.yaml').unlink()
        self.assertIsNone(self.resolve())

    def test_unknown_backend_fails_closed(self):
        self.app.agent_storage.backend = 'unknown'
        self.assertIsNone(self.resolve())

    def test_legacy_shared_and_team_precedence(self):
        legacy = self.root / 'legacy' / 'policy'
        legacy.mkdir(parents=True)
        (legacy / 'config.yaml').write_text('name: policy')
        self.resolved = legacy
        self.assertIsNone(self.resolve())
        (self.team / 'config.yaml').unlink()
        self.assertEqual(self.resolve(), 'policy')
if __name__ == '__main__':
    unittest.main()
