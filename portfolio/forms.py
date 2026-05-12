from django import forms
from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone

from portfolio.models import Client, ClientManagerAssignment, ClientPeriodFact, Manager


class ExcelImportForm(forms.Form):
    file = forms.FileField(label='Excel-файл')

    def clean_file(self):
        uploaded_file = self.cleaned_data['file']
        if not uploaded_file.name.lower().endswith(('.xlsx', '.xls')):
            raise forms.ValidationError('Загрузите файл Excel в формате .xlsx или .xls.')
        return uploaded_file


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


class ManagerForm(forms.ModelForm):
    password = forms.CharField(label='Пароль', required=False, widget=forms.PasswordInput)

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

    def save(self, commit=True):
        manager = super().save(commit=False)
        password = self.cleaned_data.get('password')
        if commit:
            manager.save()
            if manager.email:
                user, created = User.objects.get_or_create(
                    username=manager.email,
                    defaults={'email': manager.email, 'first_name': manager.full_name},
                )
                user.email = manager.email
                user.first_name = manager.full_name
                if password:
                    user.set_password(password)
                elif created:
                    user.set_unusable_password()
                user.save()
                manager.user = user
                manager.save(update_fields=['user', 'updated_at'])
        return manager
