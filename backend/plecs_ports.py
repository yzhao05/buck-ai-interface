"""Read scalar output-channel evidence from a PLECS text schematic."""
import re
from collections import defaultdict


def blocks(text, kind):
    # PLECS serializes siblings at the same indentation. Capture only direct
    # children, so a subsystem cannot shadow an outer component or connection.
    pattern = rf'^  {kind}[ \t]*\{{[ \t]*\n(.*?)^  \}}[ \t]*$'
    for match in re.finditer(pattern, text, re.M | re.S):
        yield re.sub(r'^  ', '', match[1], flags=re.M)


def field(block, name):
    found = re.search(rf'^  {name}[ \t]+(?:"([^"\n]*)"|([^\s"]+))', block, re.M)
    return (found[1] if found[1] is not None else found[2]) if found else ''


def parameters(block):
    return {field(p, 'Variable'):field(p, 'Value') for p in blocks(block, 'Parameter')}


def positive(value):
    return int(value) if re.fullmatch(r'[1-9]\d{0,2}', value) else None


def inspect_outputs(text):
    schematic = next(blocks(text, 'Schematic'), '')
    components = {}
    outputs = []
    for component in blocks(schematic, 'Component'):
        name = field(component, 'Name')
        components[name] = component if name not in components else ''
        if field(component, 'Type') == 'Output':
            params = parameters(component)
            outputs.append((positive(params.get('Index', '')), name, params.get('Width', '-1')))
    inputs = defaultdict(list)
    neighbors = defaultdict(set)
    for wire in blocks(schematic, 'Connection'):
        source = (field(wire, 'SrcComponent'), positive(field(wire, 'SrcTerminal')))
        sinks = [(n,int(t)) for n,t in re.findall(r'^\s*DstComponent[ \t]+"([^"\n]+)"\s+DstTerminal[ \t]+(\d{1,3})\b', wire, re.M)]
        if field(wire, 'Type') == 'Signal':
            for sink in sinks:
                inputs[sink].append(source)
        elif field(wire, 'Type') == 'Wire':
            for sink in sinks:
                neighbors[source].add(sink)
                neighbors[sink].add(source)

    # An electrical node can span several Wire records sharing a terminal.
    # Count the complete node when deciding whether an ammeter is in series.
    nets = {}
    for origin in neighbors:
        if origin in nets:
            continue
        pending = [origin]
        net = set()
        while pending:
            endpoint = pending.pop()
            if endpoint not in net:
                net.add(endpoint)
                pending.extend(neighbors[endpoint] - net)
        for endpoint in net:
            nets[endpoint] = net

    def meter_source(name, kind, path):
        left, right = nets.get((name,1)), nets.get((name,2))
        row = {'path':path, 'sensor':kind, 'unit':'V' if kind=='Voltmeter' else 'A'}
        candidates = []
        if left and right and left.isdisjoint(right):
            if kind == 'Voltmeter':
                candidates = [n for n,p in left if p==1 and (n,2) in right and field(components.get(n,''),'Type')=='Capacitor']
            else:
                for net, pin in ((right,1),(left,2)):
                    if len(net)==2:
                        candidates.extend(n for n,p in net if p==pin and field(components.get(n,''),'Type')=='Inductor')
        if len(set(candidates))==1:
            row.update(component=candidates[0], role='voltage_port' if kind=='Voltmeter' else 'current_port')
        else:
            row['reason'] = 'sensor found; target component or polarity requires verification'
        return [row]

    remaining = 4096
    gate_drives = defaultdict(list)
    for name, block in components.items():
        if field(block, 'Type') == 'Mosfet' and len(inputs[name,3]) == 1:
            gate_drives[inputs[name,3][0]].append(name)

    def trace(name, terminal, path):
        nonlocal remaining
        remaining -= 1
        edges = inputs[name, terminal]
        def unknown(reason):
            return [{'path':path, 'reason':reason}]
        if len(edges) != 1 or len(path) >= 12 or remaining <= 0:
            return unknown('missing, ambiguous or cyclic signal connection')
        source, pin = edges[0]
        if source in path:
            return unknown('cyclic signal connection')
        path = [*path, source]
        block = components.get(source, '')
        kind = field(block, 'Type')
        if kind == 'PlecsProbe' and pin == 1:
            rows = []
            for probe in blocks(block, 'Probe'):
                signals = re.search(r'^  Signals\s+\{([^{}]*)\}', probe, re.M)
                names = re.findall(r'"([^"\n]+)"', signals[1]) if signals else []
                if not names:
                    return unknown('unresolved Probe selection')
                for signal in names:
                    if len(rows)>=64:
                        return unknown('Probe exceeds supported channel count')
                    row = {'path':path, 'component':field(probe, 'Component'), 'component_path':field(probe, 'Path'), 'signal':signal}
                    role = {'Inductor current':'current_port','Capacitor voltage':'voltage_port'}.get(signal)
                    expected_kind = {'current_port':'Inductor','voltage_port':'Capacitor'}.get(role)
                    if role and not row['component_path'] and field(components.get(row['component'],''),'Type') == expected_kind:
                        row['role'] = role
                        row['unit'] = 'A' if role=='current_port' else 'V'
                    else:
                        row['reason'] = 'signal width or physical role requires verification'
                    rows.append(row)
            return rows or unknown('empty Probe')
        if kind in ('Ammeter','Voltmeter') and pin == 3:
            return meter_source(source, kind, path)
        if kind == 'RelationalOperator' and pin == 3 and len(gate_drives[source,pin]) == 1:
            return [{'path':path,'component':gate_drives[source,pin][0], 'signal':'Binary MOSFET gate command',
                     'unit':'0/1','role':'pwm_port'}]
        if kind == 'SignalMux' and pin == 1:
            count = positive(parameters(block).get('Width',''))
            if count and count <= 64:
                rows = []
                for input_pin in range(2, count+2):
                    rows.extend(trace(source,input_pin,path))
                    if len(rows)>64 or remaining<=0:
                        return unknown('multiplexer trace exceeds supported limits')
                return rows
        if kind == 'SignalDemux' and pin and pin >= 2:
            upstream = trace(source, 1, path)
            if positive(parameters(block).get('Width', '')) == len(upstream) and all('role' in r for r in upstream) and pin-2 < len(upstream):
                return [upstream[pin-2]]
        if kind == 'Gain' and pin == 2:
            upstream = trace(source, 1, path)
            return [{**{k:v for k,v in r.items() if k != 'role'}, 'gain':parameters(block).get('K',''), 'reason':'verify gain before binding'} for r in upstream]
        return unknown(f'unsupported or ambiguous source: {kind or "unknown"} terminal {pin}')

    result = []
    if len(outputs)>64:
        return [{'channel':None, 'reason':'more than 64 top-level outputs; inspect the model manually'}]
    indices = [index for index, _, _ in outputs]
    next_channel = 1 if sorted(i for i in indices if i) == list(range(1,len(outputs)+1)) else None
    for index, name, declared_width in sorted(outputs, key=lambda out:out[0] or 0):
        rows = trace(name, 1, [name]) if components.get(name) else [{'path':[name],'reason':'ambiguous Output name'}]
        width = positive(declared_width)
        known = (declared_width == '-1' or width == len(rows)) and all(
            'role' in r or 'sensor' in r and 'gain' not in r for r in rows)
        start = next_channel
        for offset, row in enumerate(rows):
            channel = start + offset if start and (known or width == len(rows) == 1) else None
            result.append({'output':name,'output_index':index,'channel':channel,**row})
        count = width or (len(rows) if known else None)
        next_channel = start + count if start and count else None
    return result
