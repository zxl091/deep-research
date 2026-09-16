"""Verify the published API surface without starting tasks or connecting a database."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.database import Base

with patch.object(Base.metadata, 'create_all'):
    from app_main import app
from fastapi.testclient import TestClient


class PublishedRoutesTests(unittest.TestCase):
    def test_current_workspace_routes_registered(self):
        paths = set(app.openapi()['paths'])
        for expected in ['/auth/login', '/sessions', '/knowledge-bases',
                         '/database/tables', '/assistant/runs', '/assistant/memories']:
            self.assertIn(expected, paths)

    def test_retired_entrypoints_are_not_registered(self):
        for path in app.openapi()['paths']:
            self.assertFalse(path.startswith(('/chat/', '/research/', '/news',
                '/bidding', '/attachments', '/documents/', '/search/', '/memories')),
                path)

    def test_health_and_authentication_boundary(self):
        # No lifespan context: do not run recover_interrupted or touch active jobs.
        client = TestClient(app)
        try:
            self.assertEqual(client.get('/hello').status_code, 200)
            self.assertEqual(client.get('/assistant/memories').status_code, 401)
            self.assertEqual(client.get('/knowledge-bases').status_code, 401)
        finally:
            client.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
