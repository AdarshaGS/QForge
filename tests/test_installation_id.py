from utils import installation_id as inst_module
from utils.installation_id import get_installation_id


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(inst_module, "_INSTALLATION_ID_FILE", str(tmp_path / "installation_id.txt"))


def test_first_call_generates_and_persists_an_id(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    id_1 = get_installation_id()
    assert id_1
    assert (tmp_path / "installation_id.txt").read_text().strip() == id_1


def test_repeated_calls_return_the_same_id(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    assert get_installation_id() == get_installation_id()


def test_id_persists_across_reads_of_an_existing_file(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    first = get_installation_id()

    # A totally fresh call (simulating a new app launch) must read the
    # same persisted value back, not generate a new one.
    second = get_installation_id()
    assert first == second


def test_different_installations_get_different_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(inst_module, "_INSTALLATION_ID_FILE", str(tmp_path / "a.txt"))
    id_a = get_installation_id()
    monkeypatch.setattr(inst_module, "_INSTALLATION_ID_FILE", str(tmp_path / "b.txt"))
    id_b = get_installation_id()
    assert id_a != id_b
