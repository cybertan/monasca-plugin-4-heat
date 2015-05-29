# 
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import six

from heat.common import exception
from heat.common.i18n import _
from heat.engine import constraints
from heat.engine import properties
from heat.engine import resource
from heat.engine import support
from heat.engine import watchrule

class MonascaAlarm(resource.Resource):
    PROPERTIES = (
        NAME, DESCRIPTION, EXPRESSION, MATCH_BY,
        SEVERITY, ALARM_ACTIONS, OK_ACTIONS, UNDETERMINED_ACTIONS,
    ) = (
        'name', 'description', 'expression', 'match_by',
        'severity', 'alarm_actions', 'ok_actions', 'undetermined_actions',
    )

    properties_schema = {
        NAME: properties.Schema(
            properties.Schema.STRING,
            _('Name of the alarm.'),
            required=True
        ),
        DESCRIPTION: properties.Schema(
            properties.Schema.STRING,
            _('Description of the alarm.'),
            update_allowed=True
        ),
        EXPRESSION: properties.Schema(
            properties.Schema.STRING,
            _('Expression of the alarm.'),
            required=True,
            update_allowed=True
        ),
        MATCH_BY: properties.Schema(
            properties.Schema.LIST,
            _('A list of metric dimensions to match to the alarm dimensions.'),
            update_allowed=True
        ),
        SEVERITY: properties.Schema(
            properties.Schema.STRING,
            _('Severity of the alarm.'),
            constraints=[
                constraints.AllowedValues(['LOW', 'MEDIUM', 'HIGH', 'CRITICAL']),
            ],
            default='LOW',
            update_allowed=True
        ),
        ALARM_ACTIONS: properties.Schema(
            properties.Schema.LIST,
            _('A list of notification ids that are invoked when the alarm transitions to the alarm state.'),
            update_allowed=True
        ),
        OK_ACTIONS: properties.Schema(
            properties.Schema.LIST,
            _('A list of notification ids that are invoked when the alarm transitions to the ok state.'),
            update_allowed=True
        ),
        UNDETERMINED_ACTIONS: properties.Schema(
            properties.Schema.LIST,
            _('A list of notification ids that are invoked when the alarm transitions to the undetermined state.'),
            update_allowed=True
        ), 
    }
	
    #properties_schema.update(common_properties_schema)

    default_client_name = 'monasca'
	
    def actions_to_urls(self, stack, properties):
        kwargs = {}
        for k, v in iter(properties.items()):
            if k in ['alarm_actions', 'ok_actions',
                     'undetermined_actions'] and v is not None:
                kwargs[k] = []
                for act in v:
                    # if the action is a resource name
                    # we ask the destination resource for an alarm url.
                    # the template writer should really do this in the
                    # template if possible with:
                    # {Fn::GetAtt: ['MyAction', 'AlarmUrl']}
                    if act in stack:
                        url = stack[act].FnGetAtt('AlarmUrl')
                        kwargs[k].append(url)
                    else:
                        if act:
                            kwargs[k].append(act)
            else:
                kwargs[k] = v
        return kwargs

    def cfn_to_monasca(self, stack, properties):
        """Apply all relevant compatibility xforms."""

        kwargs = self.actions_to_urls(stack, properties)
        kwargs['type'] = 'threshold'

        prefix = 'metering.'

        rule = {}
        for field in ['period', 'evaluation_periods', 'threshold',
                      'statistic', 'comparison_operator', 'meter_name']:
            if field in kwargs:
                rule[field] = kwargs[field]
                del kwargs[field]
        mmd = properties.get(self.MATCH_BY) or {}
        expression = properties.get(self.EXPRESSION) or []

        if self.MATCH_BY in kwargs:
            del kwargs[self.MATCH_BY]
        if self.EXPRESSION in kwargs:
            del kwargs[self.EXPRESSION]
        if expression:
            rule['expression'] = expression
        kwargs['threshold_rule'] = rule
        return kwargs


    def handle_create(self):
        props = self.cfn_to_monasca(self.stack,
                                       self.properties)
        props['name'] = self.physical_resource_name()
        alarm = self.monasca().alarm_definitions.create(**props)
        self.resource_id_set(alarm.alarm_id)

        # the watchrule below is for backwards compatibility.
        # 1) so we don't create watch tasks unneccessarly
        # 2) to support CW stats post, we will redirect the request
        #    to monasca.
        wr = watchrule.WatchRule(context=self.context,
                                 watch_name=self.physical_resource_name(),
                                 rule=self.parsed_template('Properties'),
                                 stack_id=self.stack.id)
        wr.state = wr.MONASCA_CONTROLLED
        wr.store()

    def handle_update(self, json_snippet, tmpl_diff, prop_diff):
        if prop_diff:
            kwargs = {'alarm_id': self.resource_id}
            kwargs.update(self.properties)
            kwargs.update(prop_diff)
            alarms_client = self.monasca().alarm_definitions
            alarms_client.update(**self.cfn_to_monasca(self.stack, kwargs))

    def handle_suspend(self):
        if self.resource_id is not None:
            self.monasca().alarm_definitions.update(alarm_id=self.resource_id,
                                            enabled=False)

    def handle_resume(self):
        if self.resource_id is not None:
            self.monasca().alarm_definitions.update(alarm_id=self.resource_id,
                                            enabled=True)

    def handle_delete(self):
        try:
            wr = watchrule.WatchRule.load(
                self.context, watch_name=self.physical_resource_name())
            wr.destroy()
        except exception.WatchRuleNotFound:
            pass

        if self.resource_id is not None:
            try:
                self.monasca().alarm_definitions.delete(self.resource_id)
            except Exception as ex:
                self.client_plugin().ignore_not_found(ex)

def resource_mapping():
    return {
        'OS::Monasca::Alarm': MonascaAlarm,
    }