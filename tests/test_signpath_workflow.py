"""Signing credential boundaries and artifact provenance, no network or signing."""
from pathlib import Path
import re
import unittest

from test_release_permissions import jobs

ROOT = Path(__file__).resolve().parents[1]


class SignPathWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = (ROOT / '.github/workflows/release.yml').read_text(encoding='utf-8')
        self.all_sections = jobs(self.workflow)
        self.sections = {name.removeprefix('test_'): body for name, body in self.all_sections.items()
                         if name.startswith('test_')}
        self.test_jobs_text = ''.join(self.sections.values())

    def assert_signing_credential_boundary(self, text):
        sections = jobs(text)
        prefix = text.split('\njobs:\n', 1)[0]
        self.assertNotIn('SIGNPATH_TEST_API_TOKEN', prefix)
        self.assertNotIn('workflow_call:', text)
        self.assertNotRegex(text, r'(?m)^\s*secrets\s*:')
        signers = {'test_sign_payload', 'test_sign_installer'}
        references = {name for name, body in sections.items() if 'secrets.' in body}
        self.assertEqual(references, signers)
        for name in signers:
            self.assertRegex(sections[name], r'(?m)^    environment: signpath-test$')
            self.assertEqual(re.findall(r'secrets\.([A-Za-z_]+)', sections[name]),
                             ['SIGNPATH_TEST_API_TOKEN', 'SIGNPATH_TEST_API_TOKEN'])
        for name in {'test_build', 'test_package', 'test_verify', 'build', 'publish'}:
            self.assertNotIn('environment:', sections[name])
            self.assertNotIn('SIGNPATH_TEST_API_TOKEN', sections[name])

    def test_only_fresh_signers_receive_the_environment_or_reference_the_token(self):
        self.assert_signing_credential_boundary(self.workflow)
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
        self.assertNotRegex(self.test_jobs_text, r'contents: write|id-token:|attestations:|secrets: inherit|pull_request_target')
        for action in re.findall(r'(?m)^\s+uses: (\S+)', self.workflow):
            self.assertRegex(action, r'^[\w.-]+/[\w.-]+@[0-9a-f]{40}$')

    def test_boundary_rejects_forwarding_or_exposing_the_token_to_build_jobs(self):
        mutants = [
            self.workflow.replace('jobs:\n',
                                  'env:\n  LEAKED_TOKEN: ${{ secrets.SIGNPATH_TEST_API_TOKEN }}\n\njobs:\n', 1),
            self.workflow.replace('  test_build:\n',
                                  '  test_build:\n    secrets:\n      SIGNPATH_TEST_API_TOKEN: ${{ secrets.SIGNPATH_TEST_API_TOKEN }}\n', 1),
            self.workflow.replace('  test_package:\n',
                                  '  test_package:\n    env:\n      LEAKED_TOKEN: ${{ secrets.SIGNPATH_TEST_API_TOKEN }}\n', 1),
            self.workflow.replace('    environment: signpath-test\n', '', 1),
        ]
        for index, mutant in enumerate(mutants):
            with self.subTest(regression=index), self.assertRaises(AssertionError):
                self.assert_signing_credential_boundary(mutant)

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
        self.assertIn('github-artifact-id: ${{ needs.test_build.outputs.payload-id }}', self.sections['sign_payload'])
        self.assertIn('github-artifact-id: ${{ needs.test_package.outputs.installer-id }}', self.sections['sign_installer'])
        for artifact_name in re.findall(r'(?m)^          name: (HaloBattery-.*)$', self.test_jobs_text):
            self.assertIn('${{ github.run_id }}-${{ github.run_attempt }}', artifact_name)
        self.assertIn('include-hidden-files: true', self.sections['build'])
        self.assertIn('needs: [test_build, test_sign_payload, test_package, test_sign_installer]', self.sections['verify'])

    def test_existing_manual_entrypoint_has_no_reusable_secret_mapping_or_publication(self):
        text = self.workflow
        sections = self.all_sections
        self.assertEqual(set(sections), {'build', 'publish', 'test_build', 'test_sign_payload',
                                        'test_package', 'test_sign_installer', 'test_verify'})
        self.assertFalse((ROOT / '.github/workflows/signpath-test.yml').exists())
        for body in self.sections.values():
            self.assertIn("if: github.event_name == 'workflow_dispatch' && inputs.signpath_test", body)
        self.assertIn("if: github.event_name != 'workflow_dispatch' || !inputs.signpath_test", sections['build'])
        self.assertIn("if: github.event_name == 'push' && github.ref_type == 'tag'", sections['publish'])
        self.assertNotIn('secrets: inherit', text)
        self.assertIn('SIGNPATH_TEST_ENABLED', self.sections['build'])
        self.assertIn("$env:GITHUB_REF -cne 'refs/heads/pre-release'", self.sections['build'])
        self.assertIn("$env:GITHUB_REPOSITORY -cne 'HeyOkay/HaloBattery'", self.sections['build'])
        self.assertIn("$env:GITHUB_EVENT_NAME -cne 'workflow_dispatch'", self.sections['build'])
        self.assertIn('CONFIRMED: ${{ inputs.signpath_test }}', self.sections['build'])
        self.assertIn("'signpath-test' || format('release-build-{0}', github.run_id)", text)
        self.assertIn('cancel-in-progress: false', text)


if __name__ == '__main__':
    unittest.main()
