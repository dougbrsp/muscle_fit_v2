from __future__ import annotations

import os
import sys

import pytest
from conftest import rodar_hook

import guarda_comandos as g

BLOQUEADOS_BASH = [
    "terraform apply",
    "terraform apply -auto-approve",
    "cd infra && terraform apply -auto-approve",
    "terraform -chdir=infra destroy",
    "terraform import aws_s3_bucket.x musclefit-x",
    "terraform state rm aws_iam_role.x",
    "terraform state mv a b",
    "TF_LOG=debug terraform apply",
    'bash -c "terraform destroy"',
    'echo "$(terraform apply)"',
    "aws lambda update-function-code --function-name x --zip-file fileb://a.zip",
    "aws --profile musclefit iam attach-role-policy --role-name r --policy-arn a",
    "aws lambda invoke --function-name x out.json --profile musclefit",
    "aws ec2 run-instances --image-id ami-1",
    "aws sns publish --topic-arn t --message m",
    "aws ec2 terminate-instances --instance-ids i-1",
    "aws secretsmanager put-secret-value --secret-id s --secret-string x",
    "aws events put-rule --name r",
    "aws rds start-db-cluster --db-cluster-identifier c",
    "aws s3 rm s3://bucket/key",
    "aws s3 cp ./arquivo s3://bucket/key",
    "aws s3 cp --exclude '*.tmp' ./dir s3://bucket/ --recursive",
    "aws s3 sync ./out s3://musclefit-frontend",
    "aws s3 mv s3://bucket/a ./local",
    "aws s3 mb s3://novo",
    "gh pr checks 5 | tail -3 && gh pr merge 5 --merge",
    "gh pr checks 430 | grep -v pass; gh pr merge 430 --squash",
    "gh pr checks 5 | tail\ngh pr merge 5",
]

PERMITIDOS_BASH = [
    'git commit -m "nao rodar terraform apply"',
    "git commit -m 'aws s3 rm s3://x e gh pr checks 1 | tail && gh pr merge 1'",
    "terraform init",
    "terraform -chdir=infra plan -out tfplan",
    "terraform validate && terraform fmt -check",
    "terraform state list",
    "terraform show tfplan",
    "aws s3 ls",
    "aws s3 ls s3://musclefit-uploads/",
    "aws lambda list-functions",
    "aws lambda list-functions --profile musclefit",
    "aws sts get-caller-identity",
    "aws sso login --profile musclefit",
    "aws logs tail /aws/lambda/musclefit-api-core --since 1h",
    "aws logs filter-log-events --log-group-name g",
    "aws s3 cp s3://bucket/key ./local",
    "aws s3 cp s3://bucket/key -",
    "aws --profile musclefit lambda get-function --function-name x",
    "aws ec2 describe-instances --region us-east-1",
    "gh pr checks 5 --watch && gh pr merge 5",
    "gh pr checks 5 --json name,bucket --jq 'all(.[]; .bucket==\"pass\")' && gh pr merge 5",
    "gh pr checks 5 | tail -3",
    "gh pr checks 5 || gh pr merge 5",
    "gh pr merge 5 --merge",
    "echo terraform apply",
    "grep -rn 'terraform apply' docs/",
    "git log --oneline -5 | grep terraform",
    # heredoc de commit (padrao do Claude Code): corpo e texto, nao comando
    "git commit -m \"$(cat <<'EOF'\nfix: nao rodar \"terraform apply\" a mao\nterraform destroy\naws s3 rm s3://x\nEOF\n)\"",
    "cat <<EOF > notas.txt\nterraform apply\naws lambda delete-function --function-name x\nEOF\necho ok",
    "cat <<-'FIM'\n\tgh pr checks 1 | tail && gh pr merge 1\n\tFIM",
    "# terraform apply\nterraform plan",
]


@pytest.mark.parametrize("cmd", BLOQUEADOS_BASH)
def test_bash_bloqueados(cmd: str) -> None:
    assert g.avaliar(cmd) is not None, cmd


@pytest.mark.parametrize("cmd", PERMITIDOS_BASH)
def test_bash_permitidos(cmd: str) -> None:
    assert g.avaliar(cmd) is None, cmd


@pytest.mark.parametrize(
    "cmd",
    [
        "terraform apply",
        "& terraform apply -auto-approve",
        "if ($ok) { terraform destroy }",
        "Set-Location infra; terraform apply",
        "aws.exe lambda update-function-code --function-name x",
        "gh pr checks 5 | Select-Object -Last 3; gh pr merge 5",
        'powershell -Command "terraform apply"',
    ],
)
def test_powershell_bloqueados(cmd: str) -> None:
    assert g.avaliar(cmd, powershell=True) is not None, cmd


@pytest.mark.parametrize(
    "cmd",
    [
        "git commit -m 'terraform apply'",
        "git commit -m @'\nterraform apply\naws s3 rm s3://x\n'@",
        "Get-ChildItem C:\\muscle_fit\\infra",
        "aws lambda list-functions --profile musclefit",
        "terraform plan",
    ],
)
def test_powershell_permitidos(cmd: str) -> None:
    assert g.avaliar(cmd, powershell=True) is None, cmd


# --- git worktree remove ------------------------------------------------------


@pytest.fixture
def worktree_com_node_modules(tmp_path):
    wt = tmp_path / "worktrees" / "agent-x"
    (wt / "frontend" / "node_modules").mkdir(parents=True)
    return wt


@pytest.fixture
def worktree_sem_node_modules(tmp_path):
    wt = tmp_path / "worktrees" / "agent-y"
    (wt / "frontend").mkdir(parents=True)
    return wt


def test_worktree_remove_com_node_modules_absoluto(worktree_com_node_modules) -> None:
    msg = g.avaliar(f'git worktree remove "{worktree_com_node_modules}"')
    assert msg is not None
    assert "cmd //c rmdir" in msg and "sem --force" in msg


def test_worktree_remove_force_tambem_bloqueia(worktree_com_node_modules) -> None:
    assert g.avaliar(f'git worktree remove --force "{worktree_com_node_modules}"') is not None


def test_worktree_remove_relativo_ao_cwd(worktree_com_node_modules) -> None:
    cwd = worktree_com_node_modules.parents[1]
    assert g.avaliar("git worktree remove worktrees/agent-x", cwd=str(cwd)) is not None


def test_worktree_remove_relativo_ao_projeto(worktree_com_node_modules) -> None:
    proj = worktree_com_node_modules.parents[1]
    assert g.avaliar("git worktree remove worktrees/agent-x", cwd=None, projeto=str(proj)) is not None


def test_worktree_remove_com_git_C(worktree_com_node_modules) -> None:
    raiz = worktree_com_node_modules.parents[1]
    assert g.avaliar(f'git -C "{raiz}" worktree remove worktrees/agent-x') is not None


def test_worktree_remove_powershell_barra_invertida(worktree_com_node_modules) -> None:
    caminho = str(worktree_com_node_modules).replace("/", "\\")
    assert g.avaliar(f"git worktree remove {caminho}", powershell=True) is not None


def test_worktree_remove_sem_node_modules_passa(worktree_sem_node_modules) -> None:
    assert g.avaliar(f'git worktree remove "{worktree_sem_node_modules}"') is None


def test_worktree_remove_caminho_inexistente_passa(tmp_path) -> None:
    assert g.avaliar(f'git worktree remove "{tmp_path / "nao-existe"}"') is None


def test_worktree_remove_variavel_passa() -> None:
    assert g.avaliar('git worktree remove "$WT"') is None


def test_worktree_list_passa(worktree_com_node_modules) -> None:
    assert g.avaliar("git worktree list") is None


# --- o script inteiro, como o Claude Code chama --------------------------------


def test_main_bloqueia_com_exit_2_e_stderr() -> None:
    r = rodar_hook("guarda_comandos.py", {"tool_name": "Bash", "tool_input": {"command": "terraform apply"}})
    assert r.returncode == 2
    assert "BLOQUEADO" in r.stderr and "pipeline" in r.stderr


def test_main_permite_com_exit_0() -> None:
    r = rodar_hook("guarda_comandos.py", {"tool_name": "Bash", "tool_input": {"command": "aws s3 ls"}})
    assert r.returncode == 0 and r.stderr == ""


def test_main_powershell() -> None:
    r = rodar_hook(
        "guarda_comandos.py",
        {"tool_name": "PowerShell", "tool_input": {"command": "& terraform destroy"}},
    )
    assert r.returncode == 2


@pytest.mark.parametrize("entrada", ["", "nao e json", "[]", '{"tool_input": "x"}', '{"tool_input": {"command": 5}}'])
def test_main_json_inesperado_nunca_trava(entrada: str) -> None:
    assert rodar_hook("guarda_comandos.py", entrada).returncode == 0


@pytest.mark.skipif(sys.platform != "win32", reason="junction so existe no Windows")
@pytest.mark.parametrize("alvo_existe", [True, False])
def test_worktree_remove_com_junction(tmp_path, alvo_existe: bool) -> None:
    """O caso real dos incidentes: frontend/node_modules e uma junction (inclusive quebrada)."""
    import _winapi

    alvo = tmp_path / "node_modules_do_usuario"
    alvo.mkdir()
    wt = tmp_path / "wt"
    (wt / "frontend").mkdir(parents=True)
    _winapi.CreateJunction(str(alvo), str(wt / "frontend" / "node_modules"))
    if not alvo_existe:
        alvo.rmdir()
    assert g.avaliar(f'git worktree remove "{wt}"') is not None
    # limpa so o link (o que o hook manda fazer), nunca seguindo a junction
    os.rmdir(wt / "frontend" / "node_modules")
