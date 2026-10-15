# Detección de empresas: cómo funciona y problemas conocidos

Código: `scraper/empresas.py`. Lo usan `build_db.py --empresas` / `--solo-empresas` (tablas `empresas`, `entradas_empresas`, `entradas_partes` para `/empresas` del server) y `cruces/cruce_qqw.py`.

## Regla de producto

Las páginas `/empresas` y `/empresas/[slug]` se indexan y **no pueden mostrar nombres de personas físicas**. Toda la detección prefiere perder una empresa antes que publicar una persona:

- Solo cuenta como empresa un trozo con forma societaria inequívoca (S.A. de C.V., S. de R.L., Sociedad Anónima, Institución de Banca, SOFOM, S.N.C., etc.). "A.C." y "S.C." solo valen al final del nombre y con al menos dos palabras antes, porque también pueden ser iniciales de una persona.
- El nombre se corta al terminar la forma societaria y sus colas conocidas. Lo que sigue se descarta ("Representada por Fulano", "hoy Fulano").
- Antes de la forma societaria se toma lo que sigue a separadores como "antes", "hoy", "vs.", "promueve", "promov.", "quejoso:", "representado por", "su apoderada", "su fiduciario", "sucesionaria", "también conocida como", un número de expediente NNN/AAAA, o un fin de oración seguido de al menos dos palabras.
- Conectores entre dos partes que separan como "y" (cada lado se evalúa por su cuenta, así una persona a la izquierda no queda pegada a la empresa): roles ("como Cesionario de", "su Cesionario", "Causahabiente por Fusión de", "Mandatario", "a través de su Mandatario", "Subrogatario", "Fusionante", "Fiduciario Sustituto"), nombres anteriores ("Anteriormente", "antes denominada", "y/o", "quien también se ostenta como"), "por derecho propio", "contra" (salvo "contra incendio", "contra la ..."), listas numeradas "1) ... 2)" y "Notario Público Número N ..., ".
- Al inicio del trozo se sacan el rol ("Cesionaria Banco X"), "Persona Moral" y el encabezado de tomo del boletín ("Tomo II", "Segundo Tomo").
- Dos nombres pegados sin separador: se corta en el último "Banco" que no sea el primero ni parte de otro nombre ("Santander Consumo Banco Santander México Sa" da "Banco Santander México Sa"; "Ixe Banco, S.A." queda igual).
- Paréntesis sin cerrar antes de la forma: se toma lo de adentro, la razón social ("Hospital X (Operadora Y, S.A. de C.V." da "Operadora Y, S.A. de C.V.").
- "X S.A. en su carácter de fiduciaria en el Fideicomiso N": si lo de la derecha no es empresa, se queda X (antes se perdía).
- Persona delante de la empresa separada por coma o por "y la / y el / y su / y por el" ("Fulano, BBVA Bancomer, S.A.", "Fulano y la Latinoamericana Seguros S.A."): se separa si lo de la izquierda tiene forma de nombre de persona con algún nombre de pila (`personas.NOMBRES_PILA`, un diccionario de nombres de pila frecuentes) y lo de la derecha es una empresa con nombre propio. Así "Mercader Financial, S.A." o "Integradora Estatal de Productores, de Chih, S.A." no se cortan.
- Roles de una persona en la empresa, con su contexto: "Secretario del Consejo de Administración de", "Director General de", "Administrador Único de", "Socio Accionista de la Moral". "Administradora de Portafolios" sigue siendo una empresa.
- Palabras pegadas por el boletín ("José AntonioSinergia") se separan antes de todo.
- Segunda red de seguridad (`persona_al_inicio`): si el nombre ya limpio empieza con un nombre de pila, o con "Apellido Apellido Nombre", y no tiene una palabra de negocio en las tres primeras ("Centro", "Industrias", "Transportes"...), la empresa no se publica. Atrapa personas pegadas por erratas que ningún separador reconoce ("Fulano Anteshsbc México S.A.", "Fulano Ycustompiec, S.A."). También pierde empresas con nombre de persona ("Silvestre Casas Grúas Rally, S.A.", despachos "José Fulano Abogados S.C."): es a propósito.
- Red de seguridad: si después de limpiar el nombre todavía trae "amparo", "promov", "quejoso", "sucesión", "albacea", "apoderado", "Lic.", "representado por", "conocida como", "vs." un número de expediente, "menor", "licenciado" o "notario", la empresa no se publica. "Menor" frena casos como "su menor hija G. S. S.A.", donde las iniciales se leen como "S.A.".
- Solo se publican empresas con al menos 2 avisos (`MIN_AVISOS_EMPRESA`): los restos de parseo casi siempre aparecen una sola vez.
- La contraparte en `/empresas/[slug]` sale solo de `entradas_empresas` (otras empresas) y del indicador de `entradas_partes` ("persona(s) física(s)"). Ninguna consulta de esas páginas lee `entradas.actora` ni `entradas.demandada`.

## Cómo se revisó (2026-09-28, base completa 2016 a 2026)

1. Muestras de 60 a 70 empresas al azar después de cada ajuste.
2. Búsqueda de patrones sospechosos en las ~150k empresas (amparo, promov, representado, conocida como, sucesión, números de expediente).

La primera versión tenía personas coladas ("Olivo Martínez Jaime antes HSBC México, S.A.", "Gutiérrez Olvera José Miguel Amparo Indirecto 885/2016 Promov. por Hipotecaria Nacional S.A."). Cada una se corrigió con una regla general, no con un caso puntual.

## Benchmark

`python3 scraper/bench_empresas.py` corre los casos etiquetados (errores ya corregidos y nuevos, con nombres de personas inventados). Con `--db <base>` compara además contra la versión del último commit (`--ref`) sobre 1 de cada 500 avisos: cuántas empresas se detectan, cuántos campos cambian, cuáles pierden alguna empresa y cuántos nombres conservan restos sospechosos (conectores, listas, tomo, notario, paréntesis sin cerrar). Antes de rearmar las tablas, todo cambio en `empresas.py` tiene que pasar los casos y revisarse con esa comparación.

Resultado del ajuste del 2026-10-14 contra el commit anterior: 24,072 a 25,336 empresas detectadas en la muestra, conectores de rol en el nombre 87 a 0, "como" / "y/o" 49 a 2, "anteriormente" 10 a 0, paréntesis sin cerrar 18 a 2. El único campo que pierde una empresa es uno que llevaba el nombre de un notario pegado.

Segundo ajuste (personas delante de la empresa), contra el commit anterior: "persona delante" (nombre de pila entre las primeras palabras) 30 a 4 en la muestra; los 15 campos que pierden una empresa son personas pegadas, salvo tres nombres de negocio con nombre de persona.

## Problemas conocidos (pendientes)

- **"y" dentro del nombre.** "y" seguido de mayúscula separa partes, también dentro de un nombre: "Sistemas y Desarrollos Rack, S.A." queda como "Desarrollos Rack, S.A." y "Banco Nacional de Obras y Servicios Públicos S N C" como "Servicios Públicos S N C".

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

## Cómo rearmar después de cambiar la detección

```bash
python scraper/build_db.py --output boletin_completo.sqlite --solo-empresas   # ~10 min, sin recargar los 34M avisos
```
