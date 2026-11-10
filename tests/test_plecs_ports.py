import asyncio
import json
import textwrap

import pytest
from backend import chat, configuration, plecs_diagnostics, plecs_local
from backend.plecs_ports import inspect_outputs
from backend.schemas import PlecsPortContext
from backend.simulation_analysis import Binding
from test_configuration import provider, request


def block(kind, body):
    return kind + ' {\n' + textwrap.indent(body, '  ') + '\n}'


def component(kind, name, extra=''):
    return block('Component', f'Type {kind}\nName "{name}"' + ('\n' + extra if extra else ''))


def parameter(key, value):
    return block('Parameter', f'Variable "{key}"\nValue "{value}"')


def wire(source, pin, sink, target):
    return block('Connection', f'Type Signal\nSrcComponent "{source}"\nSrcTerminal {pin}\n'
                 + block('Branch', f'DstComponent "{sink}"\nDstTerminal {target}'))


def model(*parts):
    probe = '\n'.join(block('Probe', f'Component "{name}"\nPath ""\nSignals {{"{signal}"}}')
                      for name,signal in [('C1','Capacitor voltage'),('L1','Inductor current')])
    schematic = '\n'.join([component('Inductor','L1'), component('Capacitor','C1'),
                           component('PlecsProbe','Probe',probe), *parts])
    return block('Plecs', 'TimeSpan "0.001"\n'+block('Schematic', schematic))


def test_selected_model_returns_vector_probe_evidence(tmp_path, monkeypatch):
    path = tmp_path/'buck.plecs'
    path.write_text(model(component('Output','Out',parameter('Index',1)),wire('Probe',1,'Out',1)))
    monkeypatch.setattr(plecs_local, 'ROOT', tmp_path/'settings')
    plecs_local.configure('owner','',str(tmp_path))
    selected = plecs_local.register('owner', str(path))
    rows = selected['output_ports']
    assert [(r['output_index'],r['channel'],r['role'],r['component']) for r in rows] == [
        (1,1,'voltage_port','C1'), (1,2,'current_port','L1')]
    assert rows == plecs_diagnostics.catalog('owner')['models'][selected['model']]['output_ports']
    settings = plecs_local.settings('owner')
    del settings['profiles'][selected['model']]['output_ports']
    plecs_local.save('owner', settings)
    assert rows == plecs_diagnostics.catalog('owner')['models'][selected['model']]['output_ports']
    settings['profiles'][selected['model']]['output_ports'] = [{'role':'current_port','channel':17}]
    plecs_local.save('owner', settings)
    assert rows == plecs_diagnostics.catalog('owner')['models'][selected['model']]['output_ports']
    path.write_text(path.read_text().replace('Inductor current','Other'))
    assert plecs_diagnostics.catalog('owner')['models'][selected['model']]['output_ports'] is None


def test_demux_and_unknown_width():
    parts = [component('SignalDemux','Split',parameter('Width',2)),
             component('Output','Second signal',parameter('Index',2)),wire('Probe',1,'Split',1),
             wire('Split',3,'Second signal',1)]
    rows = inspect_outputs(model(*parts, component('Output','First',parameter('Index',1)),
                                 wire('unknown',1,'First',1)))
    assert rows[0]['reason'] and rows[1]['channel'] is None
    rows = inspect_outputs(model(*parts, component('Output','First',parameter('Index',1)),wire('Split',2,'First',1)))
    assert [(r['channel'],r['signal']) for r in rows] == [(1,'Capacitor voltage'),(2,'Inductor current')]


def test_meter_polarity_and_gain_are_not_guessed():
    meter = component('Voltmeter','Vm')
    out = component('Output','Output1',parameter('Index',1))
    pos = wire('Vm',1,'C1',1).replace('Type Signal','Type Wire')
    neg = wire('Vm',2,'C1',2).replace('Type Signal','Type Wire')
    rows = inspect_outputs(model(meter,out,pos,neg,wire('Vm',3,'Output1',1)))
    assert rows[0]['role']=='voltage_port' and rows[0]['component']=='C1'
    inverted = [wire('Vm',i,'C1',3-i).replace('Type Signal','Type Wire') for i in (1,2)]
    rows = inspect_outputs(model(meter,out,*inverted,wire('Vm',3,'Output1',1)))
    assert 'role' not in rows[0]
    rows = inspect_outputs(model(meter,out,pos,neg,component('Gain','G',parameter('K','0.5')),
                                 wire('Vm',3,'G',1),wire('G',2,'Output1',1)))
    assert rows[0]['path']==['Output1','G','Vm'] and rows[0]['gain']=='0.5' and 'role' not in rows[0]


def test_chat_reads_catalog_then_proposes_only_verified_channels(monkeypatch):
    req = request('把模型查出的电流、电压通道写入连接设置')
    req.ui_context.plecs_ports = PlecsPortContext()
    rows = inspect_outputs(model(component('Output','Out',parameter('Index',1)),wire('Probe',1,'Out',1)))
    args = dict(model_name='buck.plecs', source='model',instruction_quote=req.message,
                changes={'current_port':2,'voltage_port':1})
    def call(name, arguments):
        return {'id':name,'type':'function','function':{'name':name,'arguments':json.dumps(arguments)}}
    provider(monkeypatch,{},[call('get_plecs_models',{}),call('configure_plecs_ports',args)])
    async def invoke(name, arguments):
        assert name=='get_plecs_models'
        return {'models':{'buck':{'description':'buck','output_ports':rows,'source_sha256':'b'*64}}}
    action = asyncio.run(chat.answer(req,invoke))['ui_action']
    assert action['type']=='configure_plecs_ports' and action['changes']==args['changes']
    assert action['source_sha256']=='b'*64
    with pytest.raises(ValueError):
        configuration.port_action(req,args,{'buck':{'description':'buck','output_ports':rows*2}})


def test_pwm_requires_evidence_that_comparator_drives_gate():
    parts = [component('Output','Pwm',parameter('Index',1)), component('RelationalOperator','Comparator'),
             component('Mosfet','T1'), wire('Comparator',3,'Pwm',1)]
    assert 'role' not in inspect_outputs(model(*parts))[0]
    row = inspect_outputs(model(*parts,wire('Comparator',3,'T1',3)))[0]
    assert (row['channel'],row['role'],row['component'])==(1,'pwm_port','T1')


def test_split_electrical_node_with_branch_is_not_a_series_current():
    parts = [component('Ammeter','Am'), component('Output','Out',parameter('Index',1)),
             wire('Am',3,'Out',1), wire('Am',2,'L1',1).replace('Type Signal','Type Wire'),
             wire('Am',1,'C1',1).replace('Type Signal','Type Wire')]
    assert inspect_outputs(model(*parts))[0]['role']=='current_port'
    branch = wire('L1',1,'C1',2).replace('Type Signal','Type Wire')
    assert 'role' not in inspect_outputs(model(*parts,branch))[0]


def test_unresolved_width_cannot_shift_subsequent_channel_numbers():
    rows = inspect_outputs(model(component('Output','First',parameter('Index',1)+'\n'+parameter('Width','N')),
                                 component('Output','Second',parameter('Index',2)),
                                 wire('Probe',1,'First',1),wire('Probe',1,'Second',1)))
    assert rows and all(row['channel'] is None for row in rows)


@pytest.mark.parametrize('quote', ['设置 case-3.plecs 的电流通道', '将电流端口改为3.5', '设置 C3 的电流通道'])
def test_port_number_requires_a_channel_number_in_the_instruction(quote):
    req = request(quote)
    req.ui_context.plecs_ports = PlecsPortContext(model_name='case-3.plecs')
    for excerpt in [quote,'3']:
        with pytest.raises(ValueError,match='port number'):
            configuration.port_action(req, dict(model_name='case-3.plecs',source='user',instruction_quote=excerpt,
                                                changes={'current_port':3}), {})


def test_binding_matches_dotted_model_and_rejects_duplicate_confirmed_ports():
    binding = Binding(model_name='BUCK.v2')
    snapshot = {'profile':{'description':'buck.v2'}, 'report':{'source_sha256':'a'*64}}
    assert binding.matches_model(snapshot)
    assert binding.model_copy(update={'source_sha256':'a'*64}).matches_model(snapshot)
    assert not binding.model_copy(update={'source_sha256':'b'*64}).matches_model(snapshot)
    assert not binding.matches_model({**snapshot, 'profile':{'description':'buck'}})
    with pytest.raises(ValueError, match='different output ports'):
        Binding(model_name='buck.plecs',current_port=1,voltage_port=1,L_parameter='L1.L',C_parameter='C1.C',confirmed=True)


def test_explicit_partial_port_edit_and_confirmation_boundary():
    req=request('仅把 PWM 输出通道清空')
    req.ui_context.plecs_ports=PlecsPortContext(model_name='buck.plecs',current_port=1,voltage_port=2,pwm_port=3)
    args=dict(model_name='buck.plecs',source='user',instruction_quote=req.message,changes={'pwm_port':None})
    assert configuration.port_action(req,args,{})['changes']=={'pwm_port':None}
    edit=request('把 PWM 改为 CH4')
    edit.ui_context.plecs_ports=req.ui_context.plecs_ports
    assert configuration.port_action(edit,{**args,'instruction_quote':edit.message,'changes':{'pwm_port':4}}, {})['changes']=={'pwm_port':4}
    with pytest.raises(ValueError):
        configuration.port_action(req,{**args,'changes':{'confirmed':True}}, {})
    assert Binding(model_name='buck.plecs',current_port=1).confirmed is False
    with pytest.raises(ValueError):
        Binding(model_name='buck.plecs',current_port=1,confirmed=True)
