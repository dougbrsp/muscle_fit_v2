# Atalhos de conveniencia para o ambiente de dev local (Docker Compose +
# LocalStack), equivalentes aos alvos do Makefile - para quem roda em
# PowerShell puro, sem `make` instalado (Git Bash/WSL). Requer Docker Desktop.
#
# Uso: .\scripts\dev.ps1 <comando>
#   up        - sobe os containers (docker compose up -d)
#   down      - derruba os containers
#   logs      - segue os logs de todos os servicos
#   db-shell  - abre um shell mysql dentro do container mysql
#   db-reset  - apaga o volume do MySQL e recria os containers do zero
#   seed      - aplica o dump real de producao (PII) - ver aviso no script Python
#   test      - roda pytest dentro do container api (NAO ha suite de testes hoje)
#
# Ver docs/local-dev.md para o guia completo.

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("up", "down", "logs", "db-shell", "db-reset", "seed", "test")]
    [string]$Command
)

# Carrega .env para expor MYSQL_USER/MYSQL_PASSWORD/MYSQL_DATABASE ao db-shell
# (docker compose ja faz isso sozinho para os proprios containers; aqui e so
# para montar o comando `docker compose exec`).
function Get-DotEnvValue([string]$Name, [string]$Default) {
    $envFile = Join-Path $PSScriptRoot "..\.env"
    if (Test-Path $envFile) {
        $line = Get-Content $envFile | Where-Object { $_ -match "^\s*$Name\s*=" } | Select-Object -Last 1
        if ($line) {
            return ($line -split "=", 2)[1].Trim()
        }
    }
    return $Default
}

switch ($Command) {
    "up" {
        docker compose up -d
    }
    "down" {
        docker compose down
    }
    "logs" {
        docker compose logs -f
    }
    "db-shell" {
        $user = Get-DotEnvValue "MYSQL_USER" "musclefit"
        $password = Get-DotEnvValue "MYSQL_PASSWORD" "musclefit"
        $database = Get-DotEnvValue "MYSQL_DATABASE" "fitness"
        docker compose exec mysql mysql "-u$user" "-p$password" $database
    }
    "db-reset" {
        docker compose down -v
        docker compose up -d
    }
    "seed" {
        docker compose exec -e SEED_WITH_PROD_DUMP=1 api python scripts/bootstrap_local_db.py
    }
    "test" {
        # NAO existe suite de testes automatizados no backend hoje - ver
        # docs/local-dev.md. Este comando roda pytest mesmo assim, para
        # quando a suite for criada.
        docker compose exec api pytest
    }
}
