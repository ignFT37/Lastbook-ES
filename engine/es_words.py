"""Lastbook ES: Spanish words for the library's keyword search.

Most of the library is in English, so a question in Spanish finds little unless its words also look for their English
equivalents. server.py merges these into STOP (words that carry no meaning in a search) and SYN (a word, then the
other words that count as a match for it), which the search page and the assistant both use (query_groups).
Each word is also added without accents, since people often type without them.
"""
import unicodedata


def _plain(w):
    return ''.join(c for c in unicodedata.normalize('NFD', w) if unicodedata.category(c) != 'Mn')


_STOP = '''a al algo algún alguna algunas alguno algunos ante antes aquí así aun aún bien cada casi como cómo con contra
cual cuál cuando cuándo de del desde donde dónde dos el él ella ellas ellos en entre era es esa ese eso esta está
están estar este esto estoy fue ha haber hace hacer hago han hasta hay he la las le les lo los más me mi mí mis mucho
muy nada ni no nos nuestra nuestro o os otra otro para pero poco por porque puede pueden puedo qué que quién quien se
sé sea según ser si sí sin sobre son su sus también tan te tengo tener ti tiene tienen todo todos tu tú tus un una
uno unos va vamos voy y ya yo debo debería hago hacemos pasa pasó quiero necesito'''.split()

# Spanish word -> English words (and Spanish variants) that should also count as a match.
_SYN = {
    # water
    'agua': ['water'], 'potable': ['drinking', 'potable', 'safe water'], 'beber': ['drinking', 'drink'],
    'grifo': ['tap', 'faucet'], 'hervir': ['boil', 'boiling'], 'hervida': ['boiled', 'boil'], 'lejía': ['bleach', 'chlorine', 'hypochlorite'],
    'cloro': ['chlorine', 'bleach'], 'potabilizar': ['purify', 'disinfect', 'water treatment'], 'purificar': ['purify', 'purification', 'disinfect'],
    'desinfectar': ['disinfect', 'disinfection'], 'filtrar': ['filter', 'filtration'], 'filtro': ['filter', 'filters'],
    'pozo': ['well', 'wells'], 'lluvia': ['rain', 'rainwater'], 'lluvias': ['rain', 'heavy rain'], 'pastillas': ['tablets', 'pills'],
    # power and light
    'apagón': ['blackout', 'outage', 'power outage'], 'luz': ['power', 'electricity', 'outage', 'light'],
    'electricidad': ['electricity', 'power'], 'corriente': ['electricity', 'power'], 'generador': ['generator', 'generators'],
    'batería': ['battery', 'batteries'], 'baterías': ['batteries', 'battery'], 'pilas': ['batteries'], 'linterna': ['flashlight', 'torch'],
    'velas': ['candles'], 'solar': ['solar', 'photovoltaic'], 'placas': ['panels', 'solar panels'], 'cargador': ['charger', 'charging'],
    'móvil': ['phone', 'cell phone', 'mobile'], 'teléfono': ['phone', 'telephone'], 'radio': ['radio'],
    # food
    'comida': ['food'], 'alimentos': ['food'], 'conservar': ['preserve', 'preservation', 'storage'], 'conservas': ['canning', 'canned'],
    'nevera': ['refrigerator', 'fridge'], 'congelador': ['freezer'], 'cocinar': ['cooking', 'cook'], 'hornillo': ['stove', 'camp stove'],
    'huerto': ['garden', 'vegetable garden'], 'semillas': ['seeds'], 'cultivar': ['grow', 'growing'],
    # shelter, heat, cold
    'refugio': ['shelter'], 'frío': ['cold', 'hypothermia', 'winter'], 'calor': ['heat', 'extreme heat'], 'calefacción': ['heating', 'heat'],
    'hipotermia': ['hypothermia'], 'congelación': ['frostbite'], 'nieve': ['snow'], 'nevada': ['snowstorm', 'snow'],
    # hazards
    'incendio': ['fire', 'wildfire'], 'incendios': ['fires', 'wildfires'], 'fuego': ['fire'], 'humo': ['smoke'],
    'inundación': ['flood', 'flooding'], 'inundaciones': ['floods', 'flooding'], 'riada': ['flash flood', 'flood'],
    'terremoto': ['earthquake'], 'sismo': ['earthquake'], 'tormenta': ['storm', 'thunderstorm'], 'viento': ['wind'],
    'tornado': ['tornado'], 'huracán': ['hurricane'], 'volcán': ['volcano', 'volcanic'], 'ceniza': ['ash'],
    'radiación': ['radiation', 'radiological'], 'nuclear': ['nuclear', 'radiological'], 'gas': ['gas', 'gas leak'],
    'monóxido': ['carbon monoxide'], 'químico': ['chemical'], 'evacuar': ['evacuate', 'evacuation'], 'evacuación': ['evacuation'],
    'confinamiento': ['shelter in place'], 'kit': ['kit', 'emergency kit'], 'mochila': ['go bag', 'emergency kit'],
    # first aid and health
    'herida': ['wound', 'wounds'], 'heridas': ['wounds', 'wound'], 'corte': ['cut', 'wound'], 'sangre': ['blood', 'bleeding'],
    'hemorragia': ['bleeding', 'haemorrhage', 'hemorrhage'], 'sangrado': ['bleeding'], 'quemadura': ['burn', 'burns'],
    'quemaduras': ['burns'], 'fractura': ['fracture'], 'hueso': ['bone', 'fracture'], 'esguince': ['sprain'],
    'rcp': ['cpr', 'resuscitation'], 'reanimación': ['resuscitation', 'cpr'], 'inconsciente': ['unconscious', 'unresponsive'],
    'desmayo': ['fainting', 'unconscious'], 'atragantamiento': ['choking'], 'atragantado': ['choking'], 'asfixia': ['choking', 'suffocation'],
    'respira': ['breathing'], 'respirar': ['breathing'], 'infarto': ['heart attack'], 'ictus': ['stroke'],
    'convulsiones': ['seizure', 'seizures'], 'fiebre': ['fever'], 'infección': ['infection'], 'diarrea': ['diarrhea', 'diarrhoea'],
    'vómitos': ['vomiting'], 'deshidratación': ['dehydration'], 'suero': ['rehydration', 'ors'], 'dolor': ['pain'],
    'medicinas': ['medicines', 'medication'], 'medicamentos': ['medicines', 'medication'], 'antibiótico': ['antibiotic', 'antibiotics'],
    'intoxicación': ['poisoning', 'poison'], 'veneno': ['poison'], 'picadura': ['sting', 'bite'], 'mordedura': ['bite'],
    'alergia': ['allergic reaction', 'allergy'], 'bebé': ['baby', 'infant'], 'niño': ['child', 'children'], 'niños': ['children'],
    'embarazada': ['pregnant', 'pregnancy'], 'parto': ['childbirth', 'birth', 'labor'], 'mayores': ['elderly', 'older people'],
    'botiquín': ['first aid kit'], 'primeros': ['first'], 'auxilios': ['aid', 'first aid'],
    # sanitation
    'váter': ['toilet', 'latrine'], 'retrete': ['toilet', 'latrine'], 'basura': ['garbage', 'waste', 'trash'],
    'higiene': ['hygiene', 'handwashing'], 'jabón': ['soap'], 'manos': ['hands', 'handwashing'],
    # repair and tools
    'reparar': ['repair', 'fix'], 'arreglar': ['repair', 'fix'], 'herramientas': ['tools'], 'motor': ['engine'],
    'bomba': ['pump'], 'cuerda': ['rope'], 'nudo': ['knot'], 'nudos': ['knots'],
    # people and places
    'coche': ['car', 'vehicle'], 'vehículo': ['vehicle', 'car'], 'casa': ['home', 'house'], 'vivienda': ['home', 'house', 'dwelling'],
    'sótano': ['basement'], 'garaje': ['garage'], 'mapa': ['map'], 'brújula': ['compass'], 'orientarse': ['navigation', 'orientation'],
}


def _with_plain(words):
    out = set()
    for w in words:
        out.add(w)
        out.add(_plain(w))
    return out


STOP = _with_plain(_STOP)
SYN = {}
for _k, _v in _SYN.items():
    for _key in {_k, _plain(_k)}:
        SYN.setdefault(_key, [])
        for _w in [_k] + _v:
            if _w != _key and _w not in SYN[_key]:
                SYN[_key].append(_w)
