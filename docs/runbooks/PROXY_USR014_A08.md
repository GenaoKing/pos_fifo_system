# A08 / USR-014 - proxies, X-Forwarded y atribucion de IP

Estado: **codigo y verificacion documental listos; preflight real pendiente**.
Este documento no autoriza consultas a Azure, cambios de ingress ni despliegues.

## Hechos verificados

[Azure Container Apps ingress](https://learn.microsoft.com/en-us/azure/container-apps/ingress-overview)
documenta que `X-Forwarded-Proto` es sobrescrito por la plataforma. Para
`X-Forwarded-For` conserva la cadena de la solicitud inicial y agrega solamente
el valor situado a la derecha; los valores anteriores deben validarse para
evitar spoofing. Por eso:

- `SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')` se usa solo
  en `settings_cloud`, el contrato de Container Apps.
- `AUDITORIA_CONFIAR_EN_PROXY` sigue en `False` por defecto. Solo habilita la
  última entrada de `X-Forwarded-For`, nunca la primera.
- La impersonación portal usa `apps.auditoria.models.get_client_ip`, la misma
  política fail-closed del rastro de auditoría. No mantiene un extractor propio
  que acepte un valor del cliente.

[Django](https://docs.djangoproject.com/en/5.2/ref/settings/#secure-proxy-ssl-header)
indica que un header de proxy solo debe confiarse cuando el proxy está bajo
control y aplica la política de sobrescritura/filtrado esperada. La conclusión
para ACA es una inferencia limitada: el extremo derecho es un dato aportado por
ACA y es apto como dirección observada por ingress; no se debe afirmar que es
siempre la IP final de una persona si existe un proxy anterior.

## Preflight autorizado pendiente

En una ventana aprobada, el operador debe conservar evidencia no sensible de:

1. Configuración de ingress efectiva y si existe Front Door, Application
   Gateway u otro proxy antes de ACA.
2. Una solicitud con una entrada izquierda de `X-Forwarded-For` controlada por
   prueba, más los valores observados de `REMOTE_ADDR`,
   `X-Forwarded-For` y `X-Forwarded-Proto` en la aplicación. No incluir
   credenciales, JWT ni query strings con secretos.
3. Que la derecha de la cadena corresponde al valor aportado por ACA y que
   `X-Forwarded-Proto=https` llega sobrescrito por ACA.
4. La decisión explícita de habilitar o mantener deshabilitado
   `AUDITORIA_CONFIAR_EN_PROXY`, junto con una prueba de login/impersonación y
   su rastro de auditoría.

Si el preflight detecta un proxy anterior, la IP se registra como dirección de
origen que ACA puede atribuir, no como identidad fuerte de usuario. Si no se
puede demostrar la cadena, mantener la variable en `False`: `REMOTE_ADDR` es
menos preciso pero no es un valor que el cliente pueda suplantar mediante una
cabecera.

## Verificacion local permitida

```powershell
python manage.py test apps.api.tests.test_proxy_headers `
  --settings=config.settings_development
git diff --check
```

Los tests fijan los dos casos: sin proxy declarado se ignora todo
`X-Forwarded-For`; con proxy declarado se toma solo la última entrada. No
prueban una topología Azure real, por lo que USR-014 no se cierra hasta ejecutar
el preflight.
