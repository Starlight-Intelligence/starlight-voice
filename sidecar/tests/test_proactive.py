import subprocess

from starlight_voice.proactive.analyzer import (
    Brief,
    RepoFinding,
    aggregate,
    build_brief,
    recommend_next_action,
    scan_repos,
    score_findings,
)


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
    _git(repo, "commit", "-qm", "init")
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


def test_aggregate_sums_across_repos() -> None:
    brief = Brief(
        date="2026-06-16",
        items=[
            RepoFinding(repo="a", branch="main", uncommitted=2, ahead=5, behind=0, no_remote=True),
            RepoFinding(repo="b", branch="main", uncommitted=1, ahead=3, behind=4, no_remote=False),
        ],
    )
    agg = aggregate(brief)
    assert agg["repos_flagged"] == 2
    assert agg["total_unpushed"] == 8
    assert agg["total_behind"] == 4
    assert agg["no_remote"] == 1


def test_recommend_next_action_picks_top_and_explains() -> None:
    brief = Brief(
        date="2026-06-16",
        items=[RepoFinding(repo="FrankX", branch="main", uncommitted=0, ahead=5, behind=0, no_remote=True)],
    )
    rec = recommend_next_action(brief)
    assert rec.startswith("Start with FrankX:")
    assert "no remote" in rec and "5 unpushed" in rec


def test_recommend_next_action_clean_is_calm() -> None:
    assert "clean" in recommend_next_action(Brief(date="2026-06-16", items=[])).lower()
