"""Signing credential boundaries and artifact provenance, no network or signing."""
from pathlib import Path
import re
import unittest

from test_release_permissions import jobs

ROOT = Path(__file__).resolve().parents[1]


class SignPathWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = (ROOT / '.github/workflows/signpath-test.yml').read_text(encoding='utf-8')
        self.sections = jobs(self.workflow)

    def test_only_fresh_signers_receive_the_environment_or_reference_the_token(self):
        self.assertEqual(set(self.sections), {'build', 'sign_payload', 'package', 'sign_installer', 'verify'})
        for name, body in self.sections.items():
            with self.subTest(job=name):
                self.assertIn('runs-on: windows-latest', body)
                if name.startswith('sign_'):
                    self.assertIn('environment: signpath-test', body)
                    self.assertIn('secrets.SIGNPATH_TEST_API_TOKEN', body)
                    self.assertNotRegex(body, r'actions/checkout|pip install|unittest|dotnet |get_inno|& \$iscc|\./tools/')
                else:
                    self.assertNotIn('environment:', body)
                    self.assertNotIn('secrets.', body)
        self.assertNotRegex(self.workflow, r'contents: write|id-token:|attestations:|secrets: inherit|pull_request_target')
        for action in re.findall(r'(?m)^\s+uses: (\S+)', self.workflow):
            self.assertRegex(action, r'^[\w.-]+/[\w.-]+@[0-9a-f]{40}$')

    def test_only_the_pinned_test_policy_and_exact_configs_are_submitted(self):
        for name, config in (('sign_payload', 'payload-test'), ('sign_installer', 'installer-test')):
            body = self.sections[name]
            self.assertIn('signing-policy-slug: test-signing', body)
            self.assertIn('artifact-configuration-slug: ' + config, body)
            self.assertIn('project-slug: HaloBattery', body)
            self.assertIn('organization-id: c54d1cb1-9c0a-478b-8eb8-036eb7084713', body)
            self.assertNotIn('signing-policy-slug: ${{', body)
        self.assertIn('TEST_CERT_THUMBPRINT: 17F9F411C424674855A077F9A120A7A434990AF1', self.workflow)

    def test_inputs_follow_exact_same_run_artifact_ids_and_reruns_are_distinct(self):
        for body in self.sections.values():
            self.assertNotRegex(body, r'run-id:|repository:|github-token:|workflow_run')
        self.assertIn('github-artifact-id: ${{ needs.build.outputs.payload-id }}', self.sections['sign_payload'])
        self.assertIn('github-artifact-id: ${{ needs.package.outputs.installer-id }}', self.sections['sign_installer'])
        for artifact_name in re.findall(r'(?m)^          name: (HaloBattery-.*)$', self.workflow):
            self.assertIn('${{ github.run_id }}-${{ github.run_attempt }}', artifact_name)
        self.assertIn('include-hidden-files: true', self.sections['build'])
        self.assertIn('needs: [build, sign_payload, package, sign_installer]', self.sections['verify'])

    def test_existing_manual_caller_maps_only_one_secret_and_cannot_publish_tests(self):
        text = (ROOT / '.github/workflows/release.yml').read_text(encoding='utf-8')
        sections = jobs(text)
        call = sections['signpath_test']
        self.assertIn("if: github.event_name == 'workflow_dispatch' && inputs.signpath_test", call)
        self.assertIn('uses: ./.github/workflows/signpath-test.yml', call)
        self.assertEqual(re.findall(r'secrets\.([A-Za-z_]+)', call), ['SIGNPATH_TEST_API_TOKEN'])
        self.assertIn("if: github.event_name != 'workflow_dispatch' || !inputs.signpath_test", sections['build'])
        self.assertIn("if: github.event_name == 'push' && github.ref_type == 'tag'", sections['publish'])
        self.assertNotIn('secrets: inherit', text)
        self.assertIn('SIGNPATH_TEST_ENABLED', self.sections['build'])
        self.assertIn("$env:GITHUB_REF -cne 'refs/heads/pre-release'", self.sections['build'])


if __name__ == '__main__':
    unittest.main()
