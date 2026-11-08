# Estado local

Os diretórios `local-state/ship/` e `local-state/ship-input/` são criados durante a operação e ignorados pelo Git. O primeiro contém plano, decisões, resultados, diffs, recibos e logs que podem incluir detalhes do código ou do submódulo privado. Trate-os como dados locais de desenvolvimento e evite incluir credenciais, payloads de usuários ou amostras de produção. O segundo guarda os JSONs de entrada do lead.

As referências duráveis do produto são a documentação versionada e o mapa dos motores. Use o histórico de execução para retomar uma entrega no mesmo checkout.
