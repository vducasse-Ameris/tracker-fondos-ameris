# fondos-tracker

Plataforma local de seguimiento de rentabilidades diarias de fondos de inversión
Ameris, ajustadas por dividendos.

## Uso diario

```
python actualizar.py
```

Trae los datos nuevos (incremental), recalcula rentabilidades y abre el reporte
HTML en el navegador. Opciones:

- `--full` — recarga toda la historia desde la fecha de inicio del fondo
- `--sin-abrir` — no abre el navegador al terminar

### Qué se actualiza a diario (matriz de frescura)

Cada corrida de `actualizar.py` refresca TODO el dato cuantitativo del dashboard:

| Dato | Fuente | Frecuencia real |
|---|---|---|
| Valor cuota, rentabilidades (diaria, MTD, 1M/3M/YTD/12M, corte fin de mes, matriz mensual, índices) | CMF valores cuota | diaria (rezago CMF 1-2 días hábiles) |
| AUM / patrimonio | CMF | diaria |
| Vol 12M, Max Drawdown 12M | calculado del índice | diaria |
| N° de aportantes | CMF valores cuota | diaria |
| Dividendos pagados | Bolsa de Santiago | diaria |
| Mayores aportantes (top 10 + mayor por fondo) | CMF pestaña 27 | diaria (el dato es trimestral; se refresca al publicarse el nuevo trimestre) |
| Presencia bursátil | Bolsa `getPresenciaBursatil` | diaria |
| Precio transado en Bolsa (solo fondos mensuales) | Bolsa `getResumenPrecios` | diaria (ver «Fondos de valorización mensual») |
| Volumen transado 12m + nivel de liquidez | Bolsa `getResumenPrecios` (fila «Año») | diaria |

### Nivel de liquidez (fila «Liquidez» de la ficha)

La ficha comparativa incluye una fila **Liquidez** que clasifica cada fondo en
**Alta / Media / Baja**, calculada en vivo:

- **Alta** — fondo **rescatable**: el inversionista rescata directo al fondo.
- **Media** — **no rescatable** con mercado secundario activo: presencia bursátil
  ≥ 25 % **o** turnover ≥ 15 % (turnover = monto transado 12m / AUM).
- **Baja** — no rescatable con baja liquidez secundaria: la única salida es vender
  la cuota en bolsa y casi no transa (ej. LAB Deuda Inmob. I, turnover ~0,5 %).

Se usa el **mejor** de los dos indicadores (presencia y turnover) porque no siempre
coinciden: un fondo puede transar mucho en pocos bloques grandes (turnover alto,
presencia baja) o al revés. La celda muestra el nivel + rescatabilidad + volumen
12m + presencia. El volumen sale de `bolsa.volumen_12m` (fila «Año» de
`getResumenPrecios`), se guarda en la tabla `volumen_bolsa` y se refresca a diario.

### Fondos de valorización mensual (categoría «Fondos inmobiliarios»)

Los fondos de deuda inmobiliaria (categoría **«Fondos inmobiliarios»**: ADI 6, BTG
Deuda Priv. Inmob. II, LAB Deuda Inmob. I) no marcan valor cuota diario como los de factoring:
ADI 6 lo publica **mensual a tasación** (cierre de mes, con rezago de semanas),
mientras BTG II y LAB I sí son diarios. El motor lo maneja con dos flags por fondo
en `fondos.json`:

- `"frecuencia": "mensual"` → la Vol 12M se anualiza con **√12** (no √252/√365) y
  se marca con **†** en la tabla de riesgo, con nota de que a tasación mensual la
  Vol/MDD salen artificialmente bajos y **no son comparables** con los fondos
  diarios. El control de frescura usa umbral de **45 días** (no 4) para no marcar
  «ATRASADO» un fondo que legítimamente reporta mensual.
- `"precio_bolsa": true` → cada serie reporta el **último precio disponible entre
  Bolsa y CMF**: si la serie transó en Bolsa (`bolsa.precio_transado`, tabla
  `precio_bolsa`) en una fecha posterior al último cierre CMF, ese precio se agrega
  como último punto del índice. Es **precio de mercado** (lleva prima/descuento
  sobre el NAV, típicamente <1%), pero es el último precio efectivamente reportado.
  Solo se captura cuando la serie realmente transó ese día (monto > 0), así que la
  historia se construye **hacia adelante**: la Bolsa no expone valor cuota histórico
  por API gratuita (es feature Premium). En ADI 6 la serie D es la líquida; A transa
  poco; B e I casi no transan y caen al valor cuota mensual de CMF.

La **ficha cualitativa** (Tipo, inversionista, dividendos-frecuencia, garantías, primera
pérdida, retorno preferente, remuneraciones, % cartera, composición) NO se actualiza a
diario: sale de reglamentos internos y factsheets (PDF, sin API, cambian rara vez). Es
**verificación periódica manual** — cada fondo lleva su fecha en `ficha_verificada`, que
el reporte muestra al pie de la ficha comparativa. Conviene re-verificar mensual/trimestral
o cuando un fondo modifica su reglamento.

`actualizar.py` imprime al final un **control de frescura**: la última fecha de valor
cuota por fondo, marcando "ATRASADO" si supera 4 días corridos (posible falla de fuente).

### Actualización automática

Existe una tarea programada de Windows **«FondosTracker actualizacion diaria»**
(creada 23-jul-2026) que ejecuta `actualizar.py --sin-abrir` todos los días a
las **09:30** (o apenas se encienda el equipo si estaba apagado) y deja
registro en `actualizacion.log`. Administrarla:

```
Get-ScheduledTaskInfo -TaskName 'FondosTracker actualizacion diaria'   # estado
Start-ScheduledTask   -TaskName 'FondosTracker actualizacion diaria'   # correr ahora
Unregister-ScheduledTask -TaskName 'FondosTracker actualizacion diaria' # eliminar
```

Para cambiar la hora: `Set-ScheduledTask` con un nuevo trigger, o desde el
Programador de tareas de Windows. Si la corrida de un día falla (sin red, etc.)
no se pierde nada: cada corrida re-consulta los últimos 7 días.

Cada corrida deja además una copia con nombre estable en la carpeta
`../link HTML/Dashboard Fondos Ameris.html` (junto al proyecto, no dentro de
`fondos-tracker`, para que sea obvia al compartir). Mismo contenido que
`reportes/reporte.html`; el nombre no cambia (el link no se rompe) y se refresca
solo todos los días. Nota: al abrirla en el navegador puede quedar cacheada —
forzar recarga con **Ctrl+F5** para ver la última versión.

### Compartir con el equipo (GitHub Pages)

Para que el equipo vea el dashboard en una **URL fija que se actualiza sola** (sin
tener el código ni recibir archivos), cada corrida publica el HTML en GitHub Pages
(`publicar.py`, llamado al final de `actualizar.py`). ⚠️ **El link de Pages en plan
gratuito es PÚBLICO e indexable** — cualquiera con la URL lo ve.

Configuración de una sola vez:

1. Crear un repositorio en GitHub, ej. `dashboard-fondos-ameris` (público).
2. Activarle Pages: **Settings → Pages → Deploy from a branch → `main` / `/root`**.
   La URL queda como `https://USUARIO.github.io/dashboard-fondos-ameris/`.
3. Crear un token de acceso (**Settings → Developer settings → Personal access
   tokens → Fine-grained**), con permiso **Contents: Read and write** sobre ese repo.
4. Clonar el repo **fuera de OneDrive** (para que la sincronización no corrompa
   `.git`), con el token embebido para que la tarea programada pueda hacer push sin
   pedir credenciales:
   ```
   git clone https://TOKEN@github.com/USUARIO/dashboard-fondos-ameris.git C:\Users\VicenteDucasse\dashboard-fondos-pages
   ```
5. El archivo `publicar.config` (junto a `publicar.py`) ya apunta a esa ruta
   `C:\Users\VicenteDucasse\dashboard-fondos-pages`. Si clonas en otra ruta, edítalo.

Listo: desde ahí, cada `python actualizar.py` copia el reporte como `index.html`,
hace commit y push, y GitHub Pages lo sirve en 1-2 min. Publicación manual suelta:
`python publicar.py`. Si `publicar.config` no existe o la ruta no es un repo git,
la publicación se omite sin afectar el resto de la corrida.

## Fuentes de datos

| Dato | Fuente | Cómo |
|---|---|---|
| Valor cuota diario por serie (incl. N° aportantes) | CMF — ficha pública del fondo, pestaña "Valores Cuota" | POST del formulario con rango de fechas (`fuentes/cmf.py`) |
| Dividendos por serie | Bolsa de Santiago / nuam — API interna (LoopBack) | `curl_cffi` con `impersonate="chrome"` para pasar el anti-bot por huella TLS + token CSRF (`fuentes/bolsa.py`) |
| 12 mayores aportantes (trimestral) | CMF — ficha del fondo, pestaña 27 "Aportantes" | **POST** (no GET) con `mm`/`aa` en el body; itera trimestres hasta el último con declaración (`cmf.obtener_mayores_aportantes`) |

## Metodología

- **Rentabilidad ajustada:** el valor cuota descuenta el dividendo típicamente
  en su **fecha límite** (Ameris FCP: 16/16 dividendos), pero el día efectivo
  puede correrse unos días (Toesca Facturas: a veces el día anterior, el
  siguiente o la fecha de pago). Por eso `rentabilidad._dia_descuento` detecta
  el día empíricamente: busca en la ventana [límite−10, pago+10] la caída del
  VC que calza con el monto (tolerancia ±50%), con la fecha límite como
  respaldo. La rentabilidad de ese día es
  `(VC_t + dividendo) / VC_{t-1} − 1`; el índice acumula estos retornos
  (dividendos reinvertidos).
- **Eventos de capital (neutralización):** en fondos de deuda privada el valor
  cuota puede saltar por un rescate/aporte grande (no rendimiento orgánico). Se
  distinguen de una revalorización real porque las *unidades* (patrimonio/VC)
  cambian de golpe ese día. Se marcan en `fondos.json` → `anomalias: [fechas]`
  por fondo, y `rentabilidad.serie_ajustada` reemplaza el retorno de ese día por
  la mediana de los ±5 días vecinos. Solo se neutralizan días verificados; las
  revalorizaciones reales (VC se mueve, unidades planas) se respetan. El script
  `escaneo_anomalias.py` (scratchpad) lista candidatos (|ret|>0,4% + Δunid>2%).
  Ejemplo: LAB Deuda Inmob. I hizo una **redenominación 1000:1** el 01-jun-2022
  (unidades ×1000, VC ÷1000) → neutralizada; su alza de +6,2% del 30-jun-2026 es
  revalorización real de cartera (unidades planas) y se respeta.
- **Repartos en cuotas (no efectivo):** algunos fondos (ej. BTG Deuda Priv.
  Inmob. I y II) reparten "equivalente en cuotas" en vez de dividendo en efectivo:
  el partícipe recibe más cuotas y el valor cuota NO baja. La Bolsa los informa con
  descripción `REPARTO EQUIV. EN $ N CUOTAS`, donde `N` son cuotas, no $/cuota;
  sumarlos al índice como efectivo lo dispara (BTG Inmob. I marcaba +524% el
  23-abr-2025). `bolsa.obtener_dividendos` descarta todo registro cuya descripción
  contenga "CUOTA".
- **Series encadenadas:** una serie comercial puede haber cambiado de nombre en
  CMF (ej: serie A = `SLPA` hasta oct-2024, luego `A`; el empalme se verificó
  continuo). El encadenamiento se declara en `fondos.json` → `series_cmf`.
- **Series excluidas** (repartos que la Bolsa no registra → rentabilidad
  ajustada saldría subestimada, u otras razones):
  - Toesca Facturas T: repartos trimestrales no registrados (jul-2025,
    oct-2025, ene-2026, detectados en el VC); S: marginal ($10M, 1 aportante)
    y ruidosa.
  - FT-Cordada R: repartos mensuales no registrados (32 caídas sin dividendo);
    RI: serie muerta (solo sep–nov 2022).
  - LarrainVial Facturas LV: no listada en Bolsa y con repartos anuales de
    cada mayo no registrados; se sigue solo la serie L.
- **Nemos múltiples:** una serie puede declarar `"nemos": [...]`; los
  dividendos duplicados entre nemos se deduplican por (fechas, monto).
  ⚠️ Verificar SIEMPRE que un nemo "alternativo" sea realmente el mismo fondo:
  `CFI-FTRUPE`/`CFI-FTRPIE` parecían renombres de Cordada CLP pero son los
  fondos hermanos USD/Mediano Plazo — sus dividendos en US$ contaminaron la
  serie P (+4,6 pb en jun-2025, +24 pb en may-2026) hasta corregirse el
  23-jul-2026. Señal de alerta: moneda del dividendo ≠ moneda del fondo, o
  valor cuota de otro orden de magnitud.
- **Mes sin publicar: se deja vacío, no se estima.** Regla del dashboard: si CMF
  no ha publicado el cierre de un mes para un fondo, ese mes va **vacío**; no se
  extrapola, no se anualiza y no se sustituye por otro mes. Dos correcciones de
  sep-2026 que violaban esto:
  - La columna de mes de la tabla «Rentabilidad y riesgo» usaba el campo
    `mes_anterior` del resumen, que se referencia al **último dato del fondo**,
    no al mes que rotula la columna. ADI 6 (valorización mensual, publica ~4
    semanas después del cierre) mostraba su **junio** bajo el rótulo «ago-26»,
    al lado de los agostos reales del resto. Ahora la columna se calcula para el
    mes que nombra y va vacía si ese fondo no lo tiene.
  - **MTD** significa mes en curso. Si el último cierre es de un mes anterior no
    hay nada que acumular: la celda va vacía. ADI 6 mostraba su julio íntegro
    como «MTD» estando ya en septiembre. Efecto lateral aceptado: los primeros
    días de cada mes, mientras CMF no publica el día 1, el MTD de todos los
    fondos sale vacío — es correcto, no hay dato del mes.

  La celda vacía va **sin marcador ni nota al pie**: no reportar es suficiente,
  y un disclaimer por fondo ensucia la tabla. Al auditar, tener presente que las
  columnas YTD y 12M de un fondo rezagado siguen siendo cifras válidas pero
  medidas a **su** último cierre, anterior al del resto de la tabla.
- **Certificación mensual:** el reporte incluye por fondo una tabla «Corte fin
  de mes» (Mes/3M/YTD/12M/24M/36M, meses calendario al último cierre mensual)
  para cotejar 1:1 contra los factsheets. Ojo: el resto del reporte usa
  ventanas rolling al último dato — no confundir cortes al auditar.
- **«Base 30» (convención de BTG):** los factsheets de BTG Asset Management
  publican la línea **«MES (Base 30)»**, que es el mes calendario escalado por
  `30 / días del mes`. En meses de 31 días la cifra publicada queda ~3% *bajo*
  el mes real y en febrero queda por encima. No es la rentabilidad efectiva del
  mes: es una normalización para comparar meses de distinto largo.
  Se declara por fondo en `fondos.json` → `"convencion_mes": "base30"`, y el
  reporte agrega la columna **Mes (Base 30)** en la tabla «Corte fin de mes» de
  esos fondos. La **matriz comparativa siempre va en mes calendario** — es la
  única convención comparable entre gestoras; mezclarlas castigaría a los
  fondos BTG en los meses de 31 días. Verificado a jul-2026: BTG Crédito
  Privado serie A, mes calendario 0,8109% → Base 30 0,78% = factsheet; BTG
  Liquidez Alternativa serie A, 0,5749% → 0,56% = factsheet.
- **Valor cuota de origen (`origen`):** CMF publica el primer valor cuota de una
  serie varios días después del inicio de la colocación, ya con devengo encima.
  Sin corregirlo, el índice arranca en ese primer cierre y la rentabilidad
  «desde inicio» sale subestimada. Declarando en `fondos.json`
  `"origen": {"fecha": "AAAA-MM-DD", "valor_cuota": 10000}` el índice se ancla
  en el par de colocación. En BTG Crédito Privado serie A esto llevó «desde
  origen» de 3,60% a **3,63%**, la cifra del factsheet. Solo se declara cuando
  el par está verificado contra el factsheet o el reglamento.
- **Acumulado anual de fondos nacidos en el año:** si el fondo no tiene cierre
  al 31-dic anterior, el YTD antes salía «–». Ahora, cuando el año de `origen`
  (o de `inicio`) es el año en curso, el acumulado anual se mide desde el origen
  — que es lo que los factsheets informan como «acumulado anual» (BTG Crédito
  Privado a jul-2026: 3,63%, igual que desde origen).
- Todo en CLP nominal.
- **Verificación (jul-2026):** los retornos mensuales, YTD y año 2024 de Ameris
  FCP series A e I replican los factsheets oficiales (sep-2025 a feb-2026)
  dentro de ±0,5 pb. El "Histórico" del factsheet parte del inicio del fondo
  (sep-2020) vía canje; el índice del tracker parte en abr-2022 (primer dato
  SLPA).

## Agregar un fondo

Agregar una entrada en `fondos.json`:

```json
"id-del-fondo": {
  "nombre": "Nombre del fondo",
  "cmf": {"rut": "9999", "tipoentidad": "FIRES"},
  "series": {
    "A": {"nemo": "CFIXXXXXA", "series_cmf": ["A"]}
  },
  "inicio": "2020-01-01"
}
```

- El `rut` de entidad CMF se obtiene de la URL de la ficha del fondo en cmfchile.cl.
- El `nemo` de cada serie se puede buscar con `fuentes/bolsa.py` →
  `ClienteBolsa().listar_cfi()` (listado completo de cuotas de fondos con razón social).
- Luego: `python actualizar.py --full`.

## Archivos

```
fondos.json      registro de fondos y series a seguir + ficha cualitativa manual
actualizar.py    comando principal (descarga + cálculo + reporte)
rentabilidad.py  índice ajustado por dividendos y resumen de rentabilidades
reporte.py       generador del reporte HTML (SVG propio, claro/oscuro)
db.py            almacenamiento SQLite (datos.db)
fuentes/cmf.py   valores cuota desde CMF
fuentes/bolsa.py dividendos desde Bolsa de Santiago
assets/          logo Ameris (embebido en el reporte como data URI)
reportes/        salida HTML
```

El reporte embebe `assets/ameris-logo.png` en el encabezado (sobre un chip
blanco para que se lea en tema claro y oscuro). Si el archivo falta, el reporte
se genera igual, sin logo.

## Reporte: dos vistas

- **Comparativo** (vista inicial): ficha cualitativa (AUM vivo + datos de
  `fondos.json → ficha`, verificados el 23-jul-2026 contra reglamentos internos
  CMF, factsheets jun-2026, EEFF e informes de clasificadoras; ver
  `ficha_verificada` por fondo), tabla de
  rentabilidad y riesgo (Vol 12M anualizada, MDD 12M) por serie comparativa
  (`fondos.json → serie_comparativa`), matriz de rentabilidad mensual (15
  meses), índice base 100 por ventana y evolución AUM mensual por fondo.
- **Detalle por fondo** (pestañas): rentabilidades por serie, **base de
  aportantes** (N° de partícipes por trimestre, todas las series → total del
  fondo), **mayores aportantes** (top 10 del último trimestre CMF: nombre, tipo,
  % propiedad), certificación a fin de mes, gráficos por ventana y dividendos.
  Privacidad: el RUT de cada aportante se guarda en la base pero NO se muestra
  en el reporte (se comparte); para uso interno se puede exponer/enmascarar.
- La matriz mensual se certificó contra el screening AATech (feb-2025 →
  mar-2026): las 74 celdas calzan ≤0,5 pb (la divergencia inicial de Cordada P
  jun-2025 era el dividendo USD contaminado; corregida).
- **Presencia bursátil** (fila de la ficha): ahora **en vivo** desde la Bolsa,
  se refresca con la actualización diaria. `bolsa.presencia_ajustada(fecha)`
  consulta `getPresenciaBursatil` (un POST trae todos los nemos con presencia),
  se guarda en la tabla `presencia_bursatil` y `reporte._presencia_fondo` arma
  el campo (series con presencia ≥25% y su %). El endpoint **no** expone market
  maker (solo el Informe Bursátil Diario, `cibe.bolsadesantiago.com/…ibdDDMMAA.pdf`),
  así que para fondos con MM (ej. Cordada) el display cae al valor estático de la
  ficha si el % en vivo es <25%.

## Dependencias

Python 3.12+ con: `requests`, `beautifulsoup4`, `pandas`, `curl_cffi`.
