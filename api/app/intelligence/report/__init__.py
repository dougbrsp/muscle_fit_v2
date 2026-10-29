"""Relatorio em Markdown (`GET /reports/markdown`).

`markdown_report_service.collect_report_data` junta os numeros dos servicos
v3 do dashboard em um dicionario simples; `markdown_renderer.render_markdown`
transforma esse dicionario em texto, sem tocar em banco.
"""
