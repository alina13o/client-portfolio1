from django import forms
from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone

from portfolio.models import Client, ClientManagerAssignment, ClientPeriodFact, Manager, Order
from portfolio.services.access import is_owner, visible_clients_for_user


class ExcelImportForm(forms.Form):
    file = forms.FileField(label='Excel-файл')
    manager = forms.ModelChoiceField(label='Менеджер', queryset=Manager.objects.none(), required=False)

    def __init__(self, *args, user=None, require_manager=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.require_manager = require_manager
        self.fields['manager'].queryset = Manager.objects.filter(is_active=True).order_by('full_name')
        self.fields['manager'].required = require_manager
        if not require_manager:
            self.fields['manager'].widget = forms.HiddenInput()

    def clean_file(self):
        uploaded_file = self.cleaned_data['file']
        if not uploaded_file.name.lower().endswith(('.xlsx', '.xls')):
            raise forms.ValidationError('Загрузите файл Excel в формате .xlsx или .xls.')
        return uploaded_file

    def clean(self):
        cleaned_data = super().clean()
        if self.require_manager and not cleaned_data.get('manager'):
            self.add_error('manager', 'Выберите менеджера для импорта.')
        return cleaned_data


class PeriodComparisonForm(forms.Form):
    class Preset(models.TextChoices):
        MONTH = 'month', 'Этот месяц vs прошлый'
        QUARTER = 'quarter', 'Этот квартал vs прошлый'
        YEAR = 'year', 'Этот год vs прошлый'
        CUSTOM = 'custom', 'Произвольные даты'

    preset = forms.ChoiceField(
        label='Сценарий сравнения',
        choices=Preset.choices,
        required=False,
        initial=Preset.MONTH,
    )
    period_1_start = forms.DateField(label='Период A: начало', widget=forms.DateInput(attrs={'type': 'date'}))
    period_1_end = forms.DateField(label='Период A: конец', widget=forms.DateInput(attrs={'type': 'date'}))
    period_2_start = forms.DateField(label='Период B: начало', widget=forms.DateInput(attrs={'type': 'date'}))
    period_2_end = forms.DateField(label='Период B: конец', widget=forms.DateInput(attrs={'type': 'date'}))

    def clean(self):
        cleaned_data = super().clean()
        for prefix in ('period_1', 'period_2'):
            start = cleaned_data.get(f'{prefix}_start')
            end = cleaned_data.get(f'{prefix}_end')
            if start and end and end < start:
                self.add_error(f'{prefix}_end', 'Дата окончания не может быть раньше даты начала.')
        return cleaned_data


class ClientForm(forms.ModelForm):
    manager = forms.ModelChoiceField(
        label='Закрепленный менеджер',
        queryset=Manager.objects.none(),
        required=False,
    )

    class Meta:
        model = Client
        fields = ['name', 'inn', 'address', 'phone', 'email', 'sales_channel', 'internal_comment', 'manager_comment']
        labels = {
            'name': 'Название',
            'inn': 'ИНН',
            'address': 'Адрес',
            'phone': 'Телефон',
            'email': 'Email',
            'sales_channel': 'Канал продаж',
            'internal_comment': 'Комментарий мой',
            'manager_comment': 'Комментарий менеджера',
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields['inn'].widget.attrs.update({'data-validate-client': 'inn'})
        self.fields['name'].widget.attrs.update({'data-validate-client': 'name'})
        self.fields['address'].widget.attrs.update({'data-validate-client': 'address'})
        self.fields['manager'].queryset = Manager.objects.filter(is_active=True)
        if user and not user.is_staff and not user.is_superuser:
            self.fields['manager'].required = False
            self.fields['manager'].widget = forms.HiddenInput()

    def clean_inn(self):
        return (self.cleaned_data.get('inn') or '').strip()

    def clean(self):
        cleaned_data = super().clean()
        inn = (cleaned_data.get('inn') or '').strip()
        name = (cleaned_data.get('name') or '').strip()
        address = (cleaned_data.get('address') or '').strip()
        queryset = Client.objects.all()
        if self.instance and self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
        if inn and queryset.filter(inn=inn).exists():
            self.add_error('inn', 'Клиент с таким ИНН уже существует.')
        if not inn and name and address and queryset.filter(name__iexact=name, address__iexact=address).exists():
            self.add_error('name', 'Клиент с таким названием и адресом уже существует.')
            self.add_error('address', 'Клиент с таким названием и адресом уже существует.')
        return cleaned_data

    def save(self, commit=True):
        client = super().save(commit=commit)
        manager = self.cleaned_data.get('manager')
        if not manager and self.user:
            manager = getattr(self.user, 'manager_profile', None)
        if commit and manager:
            ClientManagerAssignment.objects.get_or_create(
                client=client,
                manager=manager,
                valid_from=timezone.localdate(),
                defaults={'valid_to': None},
            )
        return client


class ClientPeriodFactForm(forms.ModelForm):
    class Meta:
        model = ClientPeriodFact
        fields = ['period_start', 'revenue', 'orders_count']
        labels = {
            'period_start': 'Месяц',
            'revenue': 'Сумма',
            'orders_count': 'Количество заказов',
        }
        widgets = {
            'period_start': forms.DateInput(attrs={'type': 'date'}),
        }

    def clean_period_start(self):
        value = self.cleaned_data['period_start']
        return value.replace(day=1)


class OrderForm(forms.ModelForm):
    manager = forms.ModelChoiceField(label='Менеджер', queryset=Manager.objects.none(), required=False)
    initial_comment = forms.CharField(label='Комментарий', required=False, widget=forms.Textarea(attrs={'rows': 3}))

    class Meta:
        model = Order
        fields = ['client', 'order_date', 'amount', 'cost_amount', 'positions_count', 'nomenclature_group', 'status']
        labels = {
            'client': 'Клиент',
            'order_date': 'Дата заказа',
            'amount': 'Сумма',
            'cost_amount': 'Себестоимость',
            'positions_count': 'Количество позиций',
            'nomenclature_group': 'Номенклатурная группа',
            'status': 'Статус',
        }
        widgets = {
            'order_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, user=None, require_manager=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.require_manager = require_manager
        self.fields['client'].queryset = visible_clients_for_user(user) if user else Client.objects.none()
        self.fields['manager'].queryset = Manager.objects.filter(is_active=True)
        self.fields['manager'].required = require_manager
        if user and not is_owner(user):
            self.fields['manager'].widget = forms.HiddenInput()
            self.fields['manager'].required = False

    def clean(self):
        cleaned_data = super().clean()
        client = cleaned_data.get('client')
        if self.user and client and not visible_clients_for_user(self.user).filter(id=client.id).exists():
            self.add_error('client', 'Недостаточно прав для этого клиента.')
        if self.require_manager and not cleaned_data.get('manager'):
            self.add_error('manager', 'Выберите менеджера для заказа.')
        return cleaned_data


class ManagerAccountForm(forms.ModelForm):
    login = forms.CharField(label='Логин')
    password = forms.CharField(label='Пароль', required=False, widget=forms.PasswordInput)
    is_frozen = forms.BooleanField(label='Доступ заморожен', required=False)

    class Meta:
        model = Manager
        fields = ['full_name', 'email', 'phone', 'age', 'current_year_plan', 'is_active']
        labels = {
            'full_name': 'ФИО',
            'email': 'Email',
            'phone': 'Телефон',
            'age': 'Возраст',
            'current_year_plan': 'План на год',
            'is_active': 'Активен',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['email'].widget.attrs.update({'data-validate-manager-email': '1'})
        if self.instance and self.instance.pk and self.instance.user:
            self.fields['login'].initial = self.instance.user.username
            self.fields['is_frozen'].initial = not self.instance.user.is_active
        elif self.instance and self.instance.pk:
            self.fields['login'].initial = self.instance.email or f'manager-{self.instance.pk}'

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        if not email:
            return email
        queryset = Manager.objects.filter(email__iexact=email)
        if self.instance and self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise forms.ValidationError('Менеджер с таким email уже существует.')
        return email

    def clean_login(self):
        login = (self.cleaned_data.get('login') or '').strip()
        if not login:
            raise forms.ValidationError('Укажите логин менеджера.')
        queryset = User.objects.filter(username__iexact=login)
        if self.instance and self.instance.pk and self.instance.user_id:
            queryset = queryset.exclude(pk=self.instance.user_id)
        if queryset.exists():
            raise forms.ValidationError('Пользователь с таким логином уже существует.')
        return login

    def save(self, commit=True):
        manager = super().save(commit=False)
        login = self.cleaned_data['login']
        password = self.cleaned_data.get('password')
        is_frozen = self.cleaned_data.get('is_frozen')
        if commit:
            manager.save()
            user = manager.user
            created = False
            if user is None:
                user, created = User.objects.get_or_create(
                    username=login,
                    defaults={'email': manager.email, 'first_name': manager.full_name},
                )
            user.username = login
            user.email = manager.email
            user.first_name = manager.full_name
            user.is_active = not is_frozen
            if password:
                user.set_password(password)
            elif created:
                user.set_unusable_password()
            user.save()
            manager.user = user
            manager.save(update_fields=['user', 'updated_at'])
        return manager


ManagerForm = ManagerAccountForm
