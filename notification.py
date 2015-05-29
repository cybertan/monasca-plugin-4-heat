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

class MonascaNotification(resource.Resource):
    PROPERTIES = (
        NAME, TYPE, ADDRESS,
    ) = (
        'name', 'type', 'address',
    )

    properties_schema = {
        NAME: properties.Schema(
            properties.Schema.STRING,
            _('Name of the notification.'),
            required=True
        ),
        TYPE: properties.Schema(
            properties.Schema.STRING,
            _('Type of the notification method.'),
            constraints=[
                constraints.AllowedValues(['EMAIL', 'SMS']),
            ],
            required=True,
            update_allowed=True
        ),
        ADDRESS: properties.Schema(
            properties.Schema.STRING,
            _('The email or url address to notify.'),
            required=True,
            update_allowed=True
        ) 
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

        prefix = 'metering.'

        rule = {}
        for field in ['name', 'type', 'address']:
            if field in kwargs:
                rule[field] = kwargs[field]
                del kwargs[field]
        mmd = properties.get(self.MATCHING_METADATA) or {}
        query = properties.get(self.QUERY) or []

        # make sure the matching_metadata appears in the query like this:
        # {field: metadata.$prefix.x, ...}
        for m_k, m_v in six.iteritems(mmd):
            if m_k.startswith('metadata.%s' % prefix):
                key = m_k
            elif m_k.startswith(prefix):
                key = 'metadata.%s' % m_k
            else:
                key = 'metadata.%s%s' % (prefix, m_k)
            # NOTE(prazumovsky): type of query value must be a string, but
            # matching_metadata value type can not be a string, so we
            # must convert value to a string type.
            query.append(dict(field=key, op='eq', value=six.text_type(m_v)))
        if self.MATCHING_METADATA in kwargs:
            del kwargs[self.MATCHING_METADATA]
        if self.QUERY in kwargs:
            del kwargs[self.QUERY]
        if query:
            rule['query'] = query
        kwargs['threshold_rule'] = rule
        return kwargs


    def handle_create(self):
        props = self.cfn_to_monasca(self.stack,
                                       self.properties)
        props['name'] = self.physical_resource_name()
        notification = self.monasca().notifications.create(**props)
        self.resource_id_set(notification.notification_id)

    def handle_update(self, json_snippet, tmpl_diff, prop_diff):
        if prop_diff:
            kwargs = {'alarm_id': self.resource_id}
            kwargs.update(self.properties)
            kwargs.update(prop_diff)
            notifications_client = self.monasca().notifications
            notifications_client.update(**self.cfn_to_monasca(self.stack, kwargs))

    def handle_suspend(self):
        if self.resource_id is not None:
            self.monasca().notifications.update(notification_id=self.resource_id,
                                            enabled=False)

    def handle_resume(self):
        if self.resource_id is not None:
            self.monasca().notifications.update(notification_id=self.resource_id,
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
                self.monasca().notifications.delete(self.resource_id)
            except Exception as ex:
                self.client_plugin().ignore_not_found(ex)

def resource_mapping():
    return {
        'OS::Monasca::Notification': MonascaNotification,
    }