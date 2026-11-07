# Guion operativo de la demostración (video de 5–6 minutos)

Pasos exactos, usuarios y estado inicial de cada escena. Los tiempos siguen la guía de la Evaluación 3
(0:00–0:45 contexto · 0:45–2:00 arquitectura · 2:00–4:45 demostración · 4:45–6:00 evidencia). El guion
narrado del equipo (`guion_video_entrega3.md`) no estaba en el repositorio al escribir este documento; este
runbook cubre las escenas que exige el prompt de la Evaluación 3 (§11).

## Antes de grabar

```bash
cd poc
make up          # primera vez: construye, migra, siembra y espera health checks
make demo-reset  # entre tomas: vuelve al estado inicial (BD + simuladores)
```

Abrir <http://localhost:8501>. También sirve el botón **♻️ Reiniciar demo** (Panel de demostración, pestaña 5).

**Estado inicial** (de `poc/fixtures/seed.json`):

| Dato | Valor |
|---|---|
| SR-2042 | usr-052 · CIE-L-204 · PLUMBING · `SUBMITTED` (posible duplicado de la que se crea en vivo) |
| SR-2043 / SR-2044 | usr-017 / usr-052 · ADM-201 · ELECTRICAL · `UNDER_TRIAGE` (pareja para la carrera) |
| SR-2045 | coord-03 (solicitud propia de la coordinadora) · CIE-L-101 · `SUBMITTED` |
| WO-3000 | SR-2040 · ING-B-110 · PLUMBING · `ASSIGNED` a TECH-014 |
| Próximos IDs | SR-2041, WO-3001 |
| Política vigente | POL-001 (4 h / 24 h / 72 h / 7 días) |

Usuarios del selector «Iniciar sesión como»: `usr-017` (estudiante), `usr-052` (profesor), `coord-03`,
`coord-04`, `TECH-014` (plomería), `TECH-021` (electricidad), `rep-CIE-01`, `sup-01`, `ops-01`, `dir-01` y
`coord-99` (rol inactivo).

## 0:00–0:45 · Contexto

Página **Inicio**: problema (§1.1), corte vertical (§10.1) y qué funciona hoy. Mencionar que todo es sintético.

## 0:45–2:00 · Arquitectura

1. [`diagrams.md`](diagrams.md): V-CONT (C-WEB Streamlit como cliente HTTP, C-WORKER, outbox en C-DB).
2. [`adr-009-streamlit.md`](adr-009-streamlit.md): tabla «Estado de E2-ADR-001…008» y la divergencia D-01.
3. Terminal: `make arch` → los 5 contratos de import-linter `KEPT` (T-ARCH-01; E2-ADR-002).

## 2:00–4:45 · Demostración

### Escena 1 — Flujo completo (E2-FR-01…06, Figura F19) · ~75 s

| Paso | Usuario | Página | Acción | Resultado esperado |
|---|---|---|---|---|
| 1 | usr-017 | Mis solicitudes | Nueva solicitud · CIE-L-204 · «Fuga en tubería del laboratorio» · MEDIUM · PLUMBING · Enviar de inmediato | `SR-2041` 📨 Enviada; notificación ⏳ |
| 2 | coord-03 | Triaje | Elegir SR-2041 → **Tomar para triaje** | 🔎 En triaje |
| 3 | coord-03 | Triaje | Categoría PLUMBING → aparece SR-2042 como **candidato a duplicado** (asesoría). APPROVE_CREATE_WO · HIGH · asignar TECH-014 | `WO-3001` 👷 Asignada, `due_at` = +24 h |
| 4 | TECH-014 | Mi trabajo | Iniciar → Bloquear (MATERIALS, «falta una válvula») | ⏸️ Bloqueada; SLA pausado |
| 5 | TECH-014 | Mi trabajo | Resolver bloqueo (se ve el `due_at` desplazado) → registrar material → observación → Marcar finalizada | 🕒 Pendiente de confirmación |
| 6 | rep-CIE-01 | Confirmaciones | Confirmar resultado | 🏁 Cerrada |
| 7 | usr-017 | Mis solicitudes | Ver SR-2041 | Solicitud ✅ · Orden 🏁 · Notificación 📬 (tres dimensiones, §6.8) |

**Caso alternativo o de error (validación):** coord-03 en Triaje → SR-2042 → Tomar → APPROVE_CREATE_WO
PLUMBING antes de cerrar WO-3001 → mensaje **409 DUPLICATE_CANDIDATE_EXISTS (WO-3001)** → elegir
LINK_TO_EXISTING con WO-3001 → 🔗 Vinculada. También: Rechazar sin motivo → `422 VALIDATION_FAILED`.

### Escena 2 — Carrera de dos coordinadores (QS-01, V-SEQ-02, E2-ADR-003) · ~30 s

Panel de demostración → pestaña **1** → **▶️ Lanzar carrera**. Esperado: un `201` y un
`409 DUPLICATE_CANDIDATE_EXISTS` con `existingWorkOrderId`, y **Órdenes activas en ADM-201 / ELECTRICAL = 1**.
Cuál de los dos gana no está definido. Para repetir: Reiniciar demo.

### Escena 3 — Notificaciones caídas (QS-02, V-SEQ-03, E2-ADR-004) · ~40 s

Pestaña **2**:
1. **🔴 Caer X-NOTIFY**.
2. Orden: la creada por la carrera (ADM-201). «Asignar/reasignar a» **TECH-021** → **Asignar como sup-01**.
3. Tabla en vivo (refresco cada 2 s): la orden sigue 👷 Asignada; las entregas `WorkOrderAssigned` pasan a
   🔄 En reintento con `HTTP 503` y los intentos aumentan.
4. **🟢 Restaurar X-NOTIFY** → en pocos segundos 📬 Entregada; X-NOTIFY muestra «duplicados 0».

Parámetros del worker: los del diseño (sondeo 2 s; esperas 5/10/20/30 s; lease 15 s; timeout 5 s;
12 intentos), en `.env`. Para una toma más corta se puede poner `WORKER_BACKOFF_SECONDS=2,4,8,10` y ejecutar
`make up`.

### Escena 4 — Intento no autorizado (QS-04, E2-ADR-006) · ~25 s

Pestaña **3**:
- **coord-03 triagea su propia solicitud SR-2045** → `403 ACTION_FORBIDDEN` dos veces; «SR-2045 antes → después» sin cambios.
- **TECH-021 lee la orden WO-3000** → `403 ACTION_FORBIDDEN`; eventos de auditoría de WO-3000 antes = después.

### Escena 5 — Auditoría y estado combinado (QS-03, E2-ADR-007, §6.8) · ~20 s

Pestaña **4** con `SR-2041`: tres métricas separadas y la línea de tiempo de WO-3001 (autor, rol, estado
anterior y posterior, motivo, correlación). También desde la página **Historial** como coord-03 o sup-01.

## 4:45–6:00 · Evidencia

1. [`poc/evidence/SUMMARY.md`](../../poc/evidence/SUMMARY.md): tabla PASSED / FAILED / NOT_EXECUTED con commit
   y entorno; abrir `T-CON-01.yaml` (100 ensayos) y `T-INT-01.yaml` (caída de 2 minutos).
2. [`divergences.md`](divergences.md): Streamlit (D-01) y hallazgos H-03, H-05 y H-06.
3. [`status-matrix.md`](status-matrix.md): implementado / simulado / diseñado, no implementado / pendiente;
   riesgos abiertos.
