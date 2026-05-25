REM === 1. LOGIN ===
curl -X POST http://localhost:8002/api/v1/auth/login/ ^
  -H "Content-Type: application/json" ^
  -d "{\"username\": \"Santiago\", \"password\": \"Prueba123\"}"

REM Copiar access y refresh de la respuesta:
set ACCESS=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoiYWNjZXNzIiwiZXhwIjoxNzc5MDc5NTg4LCJpYXQiOjE3NzkwNzc3ODgsImp0aSI6ImJiYzZmZDU1ZGM4YTQ1YjhiZDNjNWRiOTkyMGRiMmZhIiwidXNlcl9pZCI6IjEiLCJ1c2VybmFtZSI6IlNhbnRpYWdvIiwicm9sIjoiQURNSU4iLCJmdWxsX25hbWUiOiJTYW50aWFnbyIsInRlbmFudF9pZCI6bnVsbH0.N-aSa7ukjwNz9EUtozasJ7N6fXiNnXcCumSFZRX8jZc
set REFRESH=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoicmVmcmVzaCIsImV4cCI6MTc3OTY4MjU4OCwiaWF0IjoxNzc5MDc3Nzg4LCJqdGkiOiJlY2RkNjc0MTJiN2Q0OTVkYjY3Y2JhYTBhZWU2NGRmYyIsInVzZXJfaWQiOiIxIiwidXNlcm5hbWUiOiJTYW50aWFnbyIsInJvbCI6IkFETUlOIiwiZnVsbF9uYW1lIjoiU2FudGlhZ28iLCJ0ZW5hbnRfaWQiOm51bGx9.uB8kb33z_-SX7cZV43vgaeaezEPPTd8O2eb5KQTFbwQ

REM === 2. HIDRATAR CONTEXTO (lo que el dashboard hace en el mount inicial) ===
curl http://localhost:8002/api/v1/auth/me/ -H "Authorization: Bearer %ACCESS%"
curl http://localhost:8002/api/v1/reportes/ventas-hoy/ -H "Authorization: Bearer %ACCESS%"
curl http://localhost:8002/api/v1/sucursales/status/ -H "Authorization: Bearer %ACCESS%"

REM Esperado: tres 200 OK con datos válidos.

REM === 3. REFRESH (lo que React hace cuando el access expira a los 30 min) ===
curl -X POST http://localhost:8002/api/v1/auth/refresh/ ^
  -H "Content-Type: application/json" ^
  -d "{\"refresh\": \"%REFRESH%\"}"

REM Esperado: 200 OK con NUEVOS access y refresh (ROTATE_REFRESH_TOKENS=True).
REM Copiar el nuevo access y verificar que sigue funcionando:
set ACCESS_NEW=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoiYWNjZXNzIiwiZXhwIjoxNzc5MDgwMzAwLCJpYXQiOjE3NzkwNzg1MDEsImp0aSI6IjYzYmY1NTljNDViZTQxNmRhMDIyMzA0YTcxNTdmYTRlIiwidXNlcl9pZCI6IjEiLCJ1c2VybmFtZSI6IlNhbnRpYWdvIiwicm9sIjoiQURNSU4iLCJmdWxsX25hbWUiOiJTYW50aWFnbyIsInRlbmFudF9pZCI6bnVsbH0.nS3aWrQmc6FkOiXnMgWKl7vybplCbYNfe1lYxrN12Oc
curl http://localhost:8002/api/v1/auth/me/ -H "Authorization: Bearer %ACCESS_NEW%"

REM === 4. EDGE CASES ===

REM 4a. Token inválido
curl http://localhost:8002/api/v1/auth/me/ -H "Authorization: Bearer token_invalido"
REM Esperado: 401 con detail "Given token not valid for any token type"

REM 4b. Login con password incorrecto
curl -X POST http://localhost:8002/api/v1/auth/login/ ^
  -H "Content-Type: application/json" ^
  -d "{\"username\": \"Santiago\", \"password\": \"WRONG\"}"
REM Esperado: 401 con detail "No active account found..."