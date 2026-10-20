# Publicación de NavisCoord

La versión y el CHANGELOG se actualizan en una rama de release. Tras integrar
los cambios y verificar la CI, se crea una release de GitHub en **borrador**
con todos los assets y `SHA256SUMS.txt`.

Se inicia `firmar-release.yml` con el tag del borrador. El job
`firmar-y-publicar` usa el runner Windows `horizun-sign` y el environment
`firma-certum`, que requiere la aprobación de pablo-horizun. Pablo conecta
SimplySign Desktop y aprueba el deployment. El script envía cada binario al
servicio local de firma mediante `cola/pendiente/*.json`, espera el resultado
en `cola/hecho/*.json`, comprueba Authenticode y el sello Certum, actualiza
los hashes de los contenedores, sube los assets y publica el borrador.

Si una firma o comprobación falla, la release permanece en borrador. Se
reconecta SimplySign y se reejecuta el job fallido. Las releases publicadas son
inmutables; una corrección requiere nueva versión patch.
