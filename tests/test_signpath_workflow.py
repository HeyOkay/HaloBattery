"""Signing credential, selected-mode and verified-publication boundaries."""
from pathlib import Path
import re
import unittest
from test_release_permissions import jobs

ROOT = Path(__file__).resolve().parents[1]
SIGNERS = {f'{mode}_sign_{stage}' for mode in ('test', 'release')
           for stage in ('payload', 'internal', 'installer')}
TOKEN_FREE = {'build', 'signing_build', 'signing_prepare', 'signing_package', 'signing_verify', 'publish'}


class SignPathWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = (ROOT / '.github/workflows/release.yml').read_text(encoding='utf-8')
        self.sections = jobs(self.workflow)

    def assert_boundary(self, text):
        sections = jobs(text)
        self.assertNotIn('secrets.', text.split('\njobs:\n', 1)[0])
        self.assertNotIn('workflow_call:', text)
        self.assertNotRegex(text, r'(?m)^\s*secrets\s*:')
        self.assertEqual({name for name, body in sections.items() if 'secrets.' in body}, SIGNERS)
        for name in SIGNERS:
            body = sections[name]
            mode = name.split('_')[0]
            secret = 'SIGNPATH_TEST_API_TOKEN' if mode == 'test' else 'SIGNPATH_RELEASE_API_TOKEN'
            self.assertRegex(body, rf'(?m)^    environment: signpath-{mode}$')
            self.assertEqual(re.findall(r'secrets\.([A-Z_]+)', body), [secret, secret])
            self.assertNotRegex(body, r'actions/checkout|pip install|unittest|dotnet |get_inno|\./tools/|\./scripts/')
        for name in TOKEN_FREE:
            self.assertNotIn('environment:', sections[name])
            self.assertNotIn('secrets.', sections[name])

    def test_only_fresh_literal_signers_receive_protected_credentials(self):
        self.assertEqual(set(self.sections), SIGNERS | TOKEN_FREE)
        self.assert_boundary(self.workflow)
        for name in SIGNERS:
            self.assertIn('runs-on: windows-latest', self.sections[name])
            self.assertNotRegex(self.sections[name], r'contents: write|id-token:|attestations:')

    def test_boundary_rejects_forwarded_tokens_and_removed_protection(self):
        mutants = [
            self.workflow.replace('jobs:\n', 'env:\n  TOKEN: ${{ secrets.SIGNPATH_RELEASE_API_TOKEN }}\n\njobs:\n', 1),
            self.workflow.replace('  signing_package:\n', '  signing_package:\n    env:\n      TOKEN: ${{ secrets.SIGNPATH_TEST_API_TOKEN }}\n', 1),
            self.workflow.replace('    environment: signpath-release\n', '', 1),
            self.workflow.replace('    environment: signpath-test\n', '', 1),
            self.workflow.replace('secrets.SIGNPATH_RELEASE_API_TOKEN', 'secrets.SIGNPATH_TEST_API_TOKEN', 1),
        ]
        for index, mutant in enumerate(mutants):
            with self.subTest(regression=index), self.assertRaises(AssertionError):
                self.assert_boundary(mutant)

    def test_policy_and_configuration_are_literal_not_inputs(self):
        for mode in ('test', 'release'):
            for stage in ('payload', 'internal', 'installer'):
                body = self.sections[f'{mode}_sign_{stage}']
                self.assertIn(f'signing-policy-slug: {mode}-signing', body)
                self.assertIn(f'artifact-configuration-slug: {stage}-{mode}', body)
                self.assertIn('organization-id: c54d1cb1-9c0a-478b-8eb8-036eb7084713', body)
                self.assertIn('project-slug: HaloBattery', body)
                self.assertNotIn('signing-policy-slug: ${{', body)

    def test_inputs_come_from_the_matching_stage_of_this_run(self):
        for mode in ('test', 'release'):
            for stage, parent in (('payload', 'signing_build'), ('internal', 'signing_prepare'), ('installer', 'signing_package')):
                body = self.sections[f'{mode}_sign_{stage}']
                self.assertIn('github-artifact-id: ${{ needs.' + parent + '.outputs.' + stage + '-id }}', body)
                self.assertIn(f"needs.{parent}.result == 'success'", body)
        self.assertNotRegex(self.workflow, r'run-id:|repository:|github-token:|workflow_run:|secrets: inherit|pull_request_target')

    def test_alternate_skipped_signer_does_not_skip_verification(self):
        for name, stage in (('signing_prepare', 'payload'), ('signing_package', 'internal'), ('signing_verify', 'installer')):
            body = self.sections[name]
            condition = re.search(r'(?m)^    if: (.+)$', body)[1]
            self.assertIn('always() && !cancelled()', condition)
            for mode, other in (('test', 'release'), ('release', 'test')):
                self.assertIn(f"needs.{mode}_sign_{stage}.result == 'success'", condition)
                self.assertIn(f"needs.{other}_sign_{stage}.result == 'skipped'", condition)
            self.assertIn("throw 'Missing or mixed signing outputs'", body)
            self.assertNotRegex(body, r'outputs\.artifact-id\s*\|\|')

    def test_stable_publication_has_no_unsigned_or_test_fallback(self):
        self.assertIn("if: github.event_name == 'workflow_dispatch' && !inputs.signpath_test && !inputs.signpath_release", self.sections['build'])
        publish = self.sections['publish']
        self.assertIn('needs: signing_verify', publish)
        self.assertIn("needs.signing_verify.result == 'success'", publish)
        self.assertIn("needs.signing_verify.outputs.mode == 'Production'", publish)
        self.assertIn('needs.signing_verify.outputs.release-artifact', publish)
        self.assertNotIn('needs.build.', publish)
        self.assertNotIn('needs.test_', publish)
        final = self.sections['signing_verify']
        self.assertLess(final.index('test_signed_installer.ps1'), final.index('id: release_assets'))
        self.assertIn("if: needs.signing_build.outputs.mode == 'Production'", final)
        self.assertIn("if: needs.signing_build.outputs.mode == 'Test'", final)

    def test_helpers_are_token_free_and_actions_are_pinned(self):
        for name in ('signing_build', 'signing_prepare', 'signing_package', 'signing_verify'):
            self.assertIn('signing_pipeline.ps1', self.sections[name])
            self.assertIn('persist-credentials: false', self.sections[name])
        for action in re.findall(r'(?m)^\s+uses: (\S+)', self.workflow):
            self.assertRegex(action, r'^[\w.-]+/[\w.-]+@[0-9a-f]{40}$')
        self.assertIn('cancel-in-progress: false', self.workflow)


if __name__ == '__main__':
    unittest.main()
