# Detección de empresas: cómo funciona y problemas conocidos

Código: `scraper/empresas.py`. Lo usan `build_db.py --empresas` / `--solo-empresas` (tablas `empresas`, `entradas_empresas`, `entradas_partes` para `/empresas` en juicios-cdmx.sociedad.info) y `cruces/cruce_qqw.py`.

## Regla de producto

Las páginas `/empresas` y `/empresas/[slug]` se indexan y **no pueden mostrar nombres de personas físicas**. Toda la detección prefiere perder una empresa antes que publicar una persona:

- Solo cuenta como empresa un trozo con forma societaria inequívoca (S.A. de C.V., S. de R.L., Sociedad Anónima, Institución de Banca, SOFOM, S.N.C., etc.). "A.C." y "S.C." solo valen al final del nombre y con al menos dos palabras antes, porque también pueden ser iniciales de una persona.
- El nombre se corta al terminar la forma societaria y sus colas conocidas. Lo que sigue se descarta ("Representada por Fulano", "hoy Fulano").
- Antes de la forma societaria se toma lo que sigue a separadores como "antes", "hoy", "vs.", "promueve", "promov.", "quejoso:", "representado por", "su apoderada", "su fiduciario", "sucesionaria", "también conocida como", un número de expediente NNN/AAAA, o un fin de oración seguido de al menos dos palabras.
- Red de seguridad: si después de limpiar el nombre todavía trae "amparo", "promov", "quejoso", "sucesión", "albacea", "apoderado", "Lic.", "representado por", "conocida como", "vs." o un número de expediente, la empresa no se publica.
- Solo se publican empresas con al menos 2 avisos (`MIN_AVISOS_EMPRESA`): los restos de parseo casi siempre aparecen una sola vez.
- La contraparte en `/empresas/[slug]` sale solo de `entradas_empresas` (otras empresas) y del indicador de `entradas_partes` ("persona(s) física(s)"). Ninguna consulta de esas páginas lee `entradas.actora` ni `entradas.demandada`.

## Cómo se revisó (2026-09-28, base completa 2016 a 2026)

1. Muestras de 60 a 70 empresas al azar después de cada ajuste.
2. Búsqueda de patrones sospechosos en las ~150k empresas (amparo, promov, representado, conocida como, sucesión, números de expediente).

La primera versión tenía personas coladas ("Olivo Martínez Jaime antes HSBC México, S.A.", "Gutiérrez Olvera José Miguel Amparo Indirecto 885/2016 Promov. por Hipotecaria Nacional S.A."). Cada una se corrigió con una regla general, no con un caso puntual.

## Problemas conocidos (pendientes)

- **No hay garantía absoluta de cero personas.** La detección es por patrones, sin RFC. Se revisó por muestreo y por patrones, no nombre por nombre. Conviene sumar un control antes de pasar a público: por ejemplo, cruzar `empresas.nombre` contra un listado de nombres de pila frecuentes y revisar a mano lo que salga.
- **Variantes que no se juntan.** El núcleo es exacto: errores de tipeo del boletín ("Banco Santandaer", "Recuperacioin de Comericoi"), abreviaturas ("Gpo. Finan.") o nombres anteriores y actuales quedan como empresas distintas. Haría falta un emparejamiento aproximado (similitud por tokens) con revisión.
- **Prefijos que quedan en el nombre sin ser personas**, por ejemplo "Fiduciario del Fideicomiso Irrevocable Número …", "Integrante del Grupo Financiero Banamex …", "Institución Crediticia Banco Santander …". No exponen personas, pero el nombre visible queda feo y a veces se separa de la empresa real.
- **Formas repetidas o truncadas** en el nombre visible ("Teletech México S.A. de C.V. S.A. de C.V.", "Colegio Franco Español Sociedad de Responsabilidad"): vienen así del boletín.
- **Empresas que se pierden a propósito:**
  - núcleos de menos de 6 letras ("Beta S.A.");
  - nombres que contienen una palabra usada como separador ("Grupo Hoy Mismo S.A." se corta en "hoy");
  - empresas con un solo aviso;
  - "Banco Nacional de México, S.A. Integrante del Gpo. Finan. …" cuando no aparece una forma societaria reconocible.
- **Empresas en campos de una sola parte** (sucesiones, jurisdicciones voluntarias) se toman como actora.
- **Un mismo aviso puede nombrar a la misma empresa dos veces** en un campo: se cuenta una sola vez (`DISTINCT` en `entradas_empresas`).

- **Sufijos del expediente en Unidades de Gestión Judicial**: el número trae pegada la descripción del acuerdo ("Exp. 9518/2026, Vista Con Razon Actuarial", "Exp. 6247/2026, # No Se Admite Recurso de Apelación"). Viene del parser (`RE_ENTRY_END` toma hasta el punto), no de la detección de empresas. Separarlo en `expediente` y `descripcion` mejoraría el agrupamiento por expediente.

## Cómo rearmar después de cambiar la detección

```bash
python scraper/build_db.py --output boletin_completo.sqlite --solo-empresas        # empresas + agregados, ~20 min, sin recargar los 34M avisos
python scraper/build_db.py --output boletin_completo.sqlite --agregados-empresas   # solo tipos/años/expedientes por empresa, ~3 min
```
