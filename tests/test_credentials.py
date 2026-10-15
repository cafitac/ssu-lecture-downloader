from ssu_lecture_downloader.credentials import CredentialPair, CompositeCredentialStore, EnvCredentialStore, InMemoryCredentialStore, MacOSKeychainCredentialStore, default_credential_store


class RecordingRunner:
    def __init__(self):
        self.calls = []
        self.responses = []

    def __call__(self, args):
        self.calls.append(args)
        if self.responses:
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response
        return ''


def test_env_credential_store_reports_presence_without_values(monkeypatch):
    monkeypatch.setenv('SSU_USERNAME', 'student-id')
    monkeypatch.setenv('SSU_PASSWORD', 'secret-password')

    store = EnvCredentialStore()

    assert store.get() == CredentialPair(username='student-id', password='secret-password')
    assert store.status() == {
        'source': 'env',
        'username_present': True,
        'password_present': True,
        'can_write': False,
    }


def test_macos_keychain_store_uses_generic_password_items_without_returning_values():
    runner = RecordingRunner()
    runner.responses = ['student-id', 'secret-password', 'student-id', 'secret-password']
    store = MacOSKeychainCredentialStore(runner=runner)

    assert store.get() == CredentialPair(username='student-id', password='secret-password')
    status = store.status()

    assert status == {
        'source': 'keychain',
        'username_present': True,
        'password_present': True,
        'can_write': True,
    }
    assert runner.calls[0][:3] == ['security', 'find-generic-password', '-w']
    assert '-s' in runner.calls[0]
    assert 'ssu-lecture-downloader' in runner.calls[0]


def test_composite_store_keeps_primary_write_capability_when_env_fallback_is_used(monkeypatch):
    class EmptyWritableStore:
        def get(self):
            return None

        def status(self):
            return {'source': 'keychain', 'username_present': False, 'password_present': False, 'can_write': True}

        def save(self, pair):
            pass

        def delete(self):
            pass

    monkeypatch.setenv('SSU_USERNAME', 'student-id')
    monkeypatch.setenv('SSU_PASSWORD', 'secret-password')
    store = CompositeCredentialStore(EmptyWritableStore(), EnvCredentialStore())

    assert store.status() == {
        'source': 'env',
        'username_present': True,
        'password_present': True,
        'can_write': True,
    }


def test_macos_keychain_store_saves_and_deletes_username_and_password_accounts():
    runner = RecordingRunner()
    store = MacOSKeychainCredentialStore(runner=runner)

    store.save(CredentialPair(username='student-id', password='secret-password'))
    store.delete()

    add_calls = [call for call in runner.calls if call[:2] == ['security', 'add-generic-password']]
    delete_calls = [call for call in runner.calls if call[:2] == ['security', 'delete-generic-password']]
    assert len(add_calls) == 2
    assert len(delete_calls) == 2
    assert any('SSU_USERNAME' in call for call in add_calls)
    assert any('SSU_PASSWORD' in call for call in add_calls)
    assert any('SSU_USERNAME' in call for call in delete_calls)
    assert any('SSU_PASSWORD' in call for call in delete_calls)


def test_in_memory_store_supports_non_env_runtime_session_credentials():
    store = InMemoryCredentialStore()

    assert store.get() is None
    assert store.status() == {
        'source': 'memory',
        'username_present': False,
        'password_present': False,
        'can_write': True,
    }

    pair = CredentialPair(username='student-id', password='secret-password')
    store.save(pair)
    assert store.get() == pair
    assert store.status()['username_present'] is True

    store.delete()
    assert store.get() is None


def test_default_credential_store_uses_runtime_memory_only_even_when_env_exists(monkeypatch):
    monkeypatch.setenv('SSU_USERNAME', 'student-id')
    monkeypatch.setenv('SSU_PASSWORD', 'secret-password')

    store = default_credential_store()

    assert isinstance(store, InMemoryCredentialStore)
    assert store.get() is None
    assert store.status() == {
        'source': 'memory',
        'username_present': False,
        'password_present': False,
        'can_write': True,
    }
    pair = CredentialPair(username='runtime-id', password='runtime-password')
    store.save(pair)
    assert store.get() == pair
    assert store.status() == {
        'source': 'memory',
        'username_present': True,
        'password_present': True,
        'can_write': True,
    }
    store.delete()
    assert store.get() is None
