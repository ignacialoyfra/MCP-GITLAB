# GitLab MCP (Python)


Port en Python del servidor @zereight/mcp-gitlab, con paridad de nombres de tools y variables.


## Ejecutar local (stdio)


```bash
export $(cat .env.example | xargs) # o carga tus variables
python server.py

export STREAMABLE_HTTP=true
python server.py
# Lanza el servidor en HTTP; monta en tu host MCP soportado.






---


## 7) Notas de uso y compatibilidad
ejemplo de mcp.json
{
"mcpServers": {
"gitlab": {
"type": "stdio",
"command": "python",
"args": ["/ruta/absoluta/server.py"],
"env": {
"GITLAB_PERSONAL_ACCESS_TOKEN": "tu_token",
"GITLAB_API_URL": "https://gitlab.com",
"GITLAB_PROJECT_ID": "12345678",
"GITLAB_READ_ONLY_MODE": "false",
"USE_GITLAB_WIKI": "true",
"USE_MILESTONE": "true",
"USE_PIPELINE": "true"
}
}
}
}

- Los **nombres de tools** están homologados para que puedas migrar sin cambiar prompts/config de tus hosts MCP.
- **Modo read-only** bloquea cualquier tool con efectos de escritura.
- **Allowed projects**: si defines `GITLAB_ALLOWED_PROJECT_IDS`, solo aceptará esos IDs (y si además defines `GITLAB_PROJECT_ID`, se usa como *default*).
- **Pipelines/Milestones/Wiki** están tras flags (`USE_PIPELINE`, `USE_MILESTONE`, `USE_GITLAB_WIKI`) para reducir la superficie de herramientas cuando tu host tiene límites de tools.
- Transporte **Streamable HTTP** recomendado en despliegues persistentes; `stdio` va perfecto para agentes locales/IDE.