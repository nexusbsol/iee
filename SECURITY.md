# Política de seguridad

*(English: to report a vulnerability, use GitHub's private vulnerability reporting on this repo — see below. Please do not open a public issue.)*

## Cómo reportar una vulnerabilidad

Si encuentras un problema de seguridad en este proyecto, repórtalo de forma privada usando **[private vulnerability reporting](../../security/advisories/new)** de GitHub (pestaña *Security* → *Report a vulnerability*), en vez de abrir un issue público.

Vamos a confirmar la recepción en un plazo razonable, evaluar el impacto y coordinar contigo el momento de la divulgación una vez que exista una corrección.

## Alcance

Aplica al código de este repositorio (script `iee` y su lógica de análisis). Quedan fuera de alcance: infraestructura de terceros, dependencias externas (repórtalas directamente al proyecto correspondiente) e ingeniería social.

## Qué incluir en el reporte

- Descripción del problema y su impacto potencial.
- Pasos para reproducirlo (comando, estructura de carpetas de prueba, versión de Python).
- Si es posible, una prueba de concepto mínima.

## Versiones soportadas

Este proyecto no mantiene ramas de versiones antiguas — las correcciones de seguridad se aplican sobre `main`.
