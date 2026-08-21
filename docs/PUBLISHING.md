# Publicación en directorios

[RELEASING.md](RELEASING.md) termina con el tag y los artefactos construidos.
Este documento es el paso siguiente y es otra cosa: dónde se publica
NavisCoord para que alguien que no nos conoce lo encuentre.

Son tres canales independientes. Ninguno depende de los otros, y un fallo en
uno no bloquea a los demás.

| Canal | Qué se lista | Estado |
|---|---|---|
| Directorio de plugins de Claude | el plugin completo (MCP + skill) | listo para enviar |
| Registro oficial de MCP | el paquete de PyPI | falta publicar en PyPI |
| Directorio de conectores (MCPB) | un bundle de escritorio | listo para empaquetar y enviar |

Los tres terminan en un formulario que exige una sesión iniciada. Esa parte no
la automatiza nadie: la hace una persona con permisos en la organización.

## 1. Directorio de plugins de Claude

Es el marketplace `claude-plugins-official` que Claude Code y Cowork traen
puesto. Acepta plugins con MCP **local**, que es nuestro caso.

Antes de enviar:

```powershell
claude plugin validate .
```

El repositorio tiene que ser público — no se aceptan plugins de código
cerrado — y lo que se envía es el link de GitHub, no un zip.

Hay dos formularios y **para HorizunGroup solo sirve el de Console**:

- Console, para un autor con rol Developer, Admin u Owner:
  <https://platform.claude.com/plugins/submit>
- Claude.ai, que exige organización Team o Enterprise:
  <https://claude.ai/admin-settings/directory/submissions/plugins/new>

El segundo responde «You don't have access to organization settings» con la
cuenta actual: es una cuestión de plan, no de permisos, y no se arregla siendo
administrador de la empresa.

En Console, `/plugins/submit` redirige al alta de organización si quedó a
medias, y el paso donde se detiene es el de comprar créditos. **No hacen falta
créditos para enviar un plugin**: el botón que corresponde es «Skip for now».

Entra como plugin de comunidad con revisión automática. El sello «Anthropic
Verified» es una escalación que decide Anthropic; no se solicita.

**Después de publicado no se reenvía el formulario en cada versión**: el CI de
Anthropic espeja los cambios del repositorio al marketplace público y corre el
escrutinio automático en cada actualización.

## 2. Registro oficial de MCP

No es de Anthropic: es el índice que consumen varios clientes. Lo que se
publica es una referencia al paquete, así que el paquete tiene que existir
primero — y `naviscoord` todavía no está en PyPI.

El manifiesto es [`server.json`](../server.json) en la raíz, validado contra
el esquema `2025-12-11`.

Dos cosas que se comprueban solas y conviene entender antes de correr nada:

- **El nombre exige ser Owner.** `io.github.horizungroup/naviscoord` solo se
  concede a un Owner de la organización HorizunGroup en GitHub; pertenecer a
  la organización no basta. Si no lo eres, el registro te dará el espacio
  personal `io.github.<tu-usuario>/*` en su lugar.
- **La propiedad del paquete se verifica leyendo el README que PyPI muestra
  como descripción**, buscando ahí la cadena `mcp-name:
  io.github.horizungroup/naviscoord`. El marcador ya está en
  [README.md](../README.md), pero viaja dentro del wheel y del sdist: si
  subes a PyPI un artefacto construido **antes** de que se agregara, la
  verificación falla y no hay forma de arreglarlo salvo publicar otra versión.

Orden:

```powershell
python scripts\build_artifacts.py
```

```powershell
python -m twine upload server\dist\naviscoord-0.4.1*
```

El token de la **primera** subida tiene que ser de alcance «Entire account»:
PyPI no deja acotar un token a un proyecto que todavía no existe. En cuanto
`naviscoord` esté publicado conviene emitir uno acotado y retirar el otro.

La alternativa es Trusted Publishing, que sube desde GitHub Actions por OIDC y
elimina el token. No está implementado a propósito: el `ci.yml` de este
repositorio declara que ningún job publica nada, y activar OIDC significa dar
`id-token: write` a un workflow. Es una decisión de postura, no una tarea
pendiente.

```powershell
mcp-publisher login github
```

```powershell
mcp-publisher publish --dry-run
```

```powershell
mcp-publisher publish
```

La versión de `server.json` debe coincidir con la que quedó en PyPI, y hay que
subirla en cada release igual que el resto de las declaraciones de versión.

## 3. Directorio de conectores, como extensión de escritorio

El portal de conectores acepta **solo servidores remotos**, así que por ahí
NavisCoord no entra: necesita el Navisworks que corre en la misma máquina. La
puerta para un servidor local es el bundle MCPB, que es lo que Claude Desktop
instala desde Settings → Extensions → Browse extensions, y tiene su propio
formulario.

Armar el bundle:

```powershell
python scripts\build_mcpb.py --pack
```

`--pack` necesita el CLI oficial (`npm install -g @anthropic-ai/mcpb`). Sin
él, el script deja el árbol en `dist/mcpb` y dice qué falta.

Lo que la revisión exige y aquí ya está resuelto:

- **Política de privacidad**, sección en el README y `privacy_policies` en el
  manifiesto, con URL HTTPS. Falta o incompleta es rechazo inmediato.
- **Anotaciones en todas las tools**: `title` más `readOnlyHint` o
  `destructiveHint`. Las 50 las llevan, y dos tests lo sostienen
  (`test_every_tool_is_annotated`, `test_no_writing_tool_claims_to_be_read_only`).
- **Lectura y escritura separadas**, sin una tool que haga ambas según un
  parámetro.
- **Código abierto**, que no es negociable para MCPB.

El icono es [`assets/icon.png`](../assets/icon.png), generado por
[`scripts/make_icon.py`](../scripts/make_icon.py) sobre el `brandColor` que ya
declaraba el manifiesto de Codex. El manifiesto del bundle lo declara y el
mismo archivo sirve para subir en los formularios.

Lo único que no está en el repositorio es un **correo de soporte público**:
hoy el canal es issues de GitHub. Los formularios piden además un contacto de
revisión, que se escribe ahí y no vive en el código.

Formulario: <https://clau.de/desktop-extention-submission>

## Antes de cualquier envío

```powershell
cd server
python -m pytest tests -q
```

```powershell
claude plugin validate ..
```

Los criterios completos que aplica la revisión están en
<https://claude.com/docs/connectors/building/review-criteria>.
