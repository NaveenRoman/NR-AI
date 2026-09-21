"""
Dedicated Unit & Integration Tests: Prompt, PII, and Secret Guardrails.
Tests detection and structured redaction of credentials, API keys, tokens, PII, and context preservation.
"""

import unittest

from app.security.guardrails import (
    GuardrailsEngine,
    RedactionMode,
    SecurityBlockError,
    global_guardrails,
)


class TestPromptGuardrails(unittest.TestCase):

    def setUp(self):
        self.engine = GuardrailsEngine(mode=RedactionMode.REDACT)

    def test_openai_key_redaction(self):
        text = "Please test sk-1234567890abcdef1234567890abcdef with the model."
        res = self.engine.scan(text)
        self.assertFalse(res.is_clean)
        self.assertEqual(len(res.findings), 1)
        self.assertEqual(res.findings[0].pattern_name, "OPENAI_KEY")
        self.assertIn("[SECRET_REDACTED:OPENAI_KEY]", res.redacted_text)
        self.assertNotIn("sk-1234567890abcdef1234567890abcdef", res.redacted_text)

    def test_google_aiza_key_redaction(self):
        text = "Google API Key is AIzaSyD123456789012345678901234567890"
        redacted = self.engine.redact(text)
        self.assertIn("[SECRET_REDACTED:GOOGLE_API_KEY]", redacted)

    def test_bearer_token_redaction(self):
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.doNotLeakThis"
        redacted = self.engine.redact(text)
        self.assertIn("Bearer [SECRET_REDACTED:BEARER_TOKEN]", redacted)

    def test_private_key_redaction(self):
        text = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEowIBAAKCAQEA0Y123456789abcdef\n"
            "-----END RSA PRIVATE KEY-----"
        )
        res = self.engine.scan(text)
        self.assertFalse(res.is_clean)
        self.assertIn("[SECRET_REDACTED:PRIVATE_KEY]", res.redacted_text)

    def test_pii_redaction(self):
        text = "Contact user at john.doe@example.com, SSN 123-45-6789, card 4111 1111 1111 1111"
        res = self.engine.scan(text)
        self.assertFalse(res.is_clean)
        self.assertEqual(len(res.findings), 3)
        self.assertIn("[PII_REDACTED:EMAIL]", res.redacted_text)
        self.assertIn("[PII_REDACTED:US_SSN]", res.redacted_text)
        self.assertIn("[PII_REDACTED:CREDIT_CARD]", res.redacted_text)
        self.assertNotIn("john.doe@example.com", res.redacted_text)
        self.assertNotIn("123-45-6789", res.redacted_text)

    def test_password_assignment_preserves_variable_syntax(self):
        text = 'db_pass = "SuperSecretPassword123!"'
        redacted = self.engine.redact(text)
        self.assertIn('db_pass = "[SECRET_REDACTED:PASSWORD]"', redacted)
        self.assertNotIn("SuperSecretPassword123!", redacted)

    def test_block_mode_raises_exception(self):
        engine = GuardrailsEngine(mode=RedactionMode.BLOCK)
        messages = [
            {"role": "user", "content": "Here is my private token sk-ant-1234567890abcdef1234567890"}
        ]
        with self.assertRaises(SecurityBlockError):
            engine.sanitize_payload(messages)

    def test_clean_engineering_context_preserved(self):
        code = """
        class AuthService:
            def __init__(self, api_key_name: str = "OPENAI_API_KEY"):
                self.api_key_name = api_key_name
            def get_auth(self) -> bool:
                return True
        """
        res = self.engine.scan(code)
        self.assertTrue(res.is_clean)
        self.assertEqual(res.redacted_text.strip(), code.strip())


if __name__ == "__main__":
    unittest.main()
