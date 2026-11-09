import subprocess

from starlight_voice.proactive.analyzer import RepoFinding, build_brief, scan_repos, score_findings


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)


def _make_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@t.t")
    _git(path, "config", "user.name", "t")
    return path


def test_clean_repo_with_remote_scores_zero_and_is_dropped() -> None:
    # truly clean = committed AND has a remote AND in sync; scores 0 -> dropped
    f = RepoFinding(repo="x", branch="main", uncommitted=0, ahead=0, behind=0, no_remote=False)
    assert f.score == 0
    assert score_findings([f]) == []


def test_local_only_clean_repo_still_surfaces(tmp_path) -> None:
    # a committed repo with NO remote is actionable (can't survive disk failure)
    repo = _make_repo(tmp_path / "clean")
    (repo / "f.txt").write_text("x", encoding="utf-8")
    _git(repo, "add", "-A")
    # Plumbing, not `git commit`: the machine's commit hooks refuse unclaimed throwaway repos.
    tree = subprocess.run(["git", "-C", str(repo), "write-tree"], capture_output=True, text=True).stdout.strip()
    sha = subprocess.run(["git", "-C", str(repo), "commit-tree", tree, "-m", "init"], capture_output=True, text=True).stdout.strip()
    _git(repo, "update-ref", "HEAD", sha)
    findings = scan_repos([repo])
    assert findings[0].uncommitted == 0
    assert findings[0].no_remote is True
    assert findings[0].score == 5  # no-remote risk weight


def test_uncommitted_and_no_remote_surface(tmp_path) -> None:
    repo = _make_repo(tmp_path / "dirty")
    (repo / "f.txt").write_text("x", encoding="utf-8")  # untracked, no commit, no remote
    brief = build_brief([repo], "2026-06-15")
    assert brief.items, "dirty repo should surface"
    top = brief.items[0]
    assert top.no_remote is True
    assert top.uncommitted >= 1
    assert top.score >= 5  # no-remote weight
    assert "dirty" in brief.headline()


def test_non_git_path_ignored(tmp_path) -> None:
    (tmp_path / "plain").mkdir()
    assert scan_repos([tmp_path / "plain"]) == []
