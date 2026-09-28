from django import forms
from django.contrib.auth.password_validation import validate_password
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import SavedPassenger, User
from apps.accounts.services import InvalidPhoneNumberError, normalize_phone
from apps.bookings.models import BookingPassenger
from apps.payments.models import Payment
from apps.routes.models import City, RouteStop


class BootstrapFormMixin:
    """Adds Bootstrap's form-control class to every field's widget, so plain
    Django forms render styled without a template-tag library dependency."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = (existing + " form-control").strip()


class TripSearchForm(BootstrapFormMixin, forms.Form):
    origin = forms.ModelChoiceField(
        label=_("From"), queryset=City.objects.order_by("name_en"), to_field_name="public_id"
    )
    destination = forms.ModelChoiceField(
        label=_("To"), queryset=City.objects.order_by("name_en"), to_field_name="public_id"
    )
    date = forms.DateField(label=_("Date"), widget=forms.DateInput(attrs={"type": "date"}))
    passengers = forms.IntegerField(label=_("Passengers"), min_value=1, max_value=10, initial=1)

    def clean(self):
        cleaned_data = super().clean()
        origin = cleaned_data.get("origin")
        destination = cleaned_data.get("destination")
        if origin and destination and origin == destination:
            self.add_error("destination", _("Origin and destination must be different."))
        return cleaned_data


class SeatSelectionForm(forms.Form):
    seat_ids = forms.CharField(widget=forms.HiddenInput)

    def clean_seat_ids(self):
        raw = self.cleaned_data["seat_ids"]
        ids = [s for s in raw.split(",") if s]
        if not ids:
            raise forms.ValidationError(_("Select at least one seat."))
        return ids


class BoardingStopForm(BootstrapFormMixin, forms.Form):
    boarding_stop = forms.ModelChoiceField(label=_("Boarding stop"), queryset=RouteStop.objects.none())
    drop_stop = forms.ModelChoiceField(label=_("Drop-off stop"), queryset=RouteStop.objects.none())

    def __init__(self, *args, route_stops=None, **kwargs):
        super().__init__(*args, **kwargs)

        def label(route_stop):
            name = route_stop.stop.name_km if get_language() == "km" else route_stop.stop.name_en
            return f"{name} (+{route_stop.offset_minutes} min)"

        qs = route_stops if route_stops is not None else RouteStop.objects.none()
        self.fields["boarding_stop"].queryset = qs
        self.fields["drop_stop"].queryset = qs
        self.fields["boarding_stop"].label_from_instance = label
        self.fields["drop_stop"].label_from_instance = label

    def clean(self):
        cleaned_data = super().clean()
        boarding_stop = cleaned_data.get("boarding_stop")
        drop_stop = cleaned_data.get("drop_stop")
        if boarding_stop and drop_stop and boarding_stop.sequence >= drop_stop.sequence:
            self.add_error("drop_stop", _("The drop-off stop must come after the boarding stop."))
        return cleaned_data


class CheckoutContactForm(BootstrapFormMixin, forms.Form):
    contact_phone = forms.CharField(label=_("Contact phone"), max_length=16)
    contact_email = forms.EmailField(label=_("Contact email"), required=False)

    def clean_contact_phone(self):
        raw = self.cleaned_data["contact_phone"]
        try:
            return normalize_phone(raw)
        except InvalidPhoneNumberError as exc:
            raise forms.ValidationError(str(exc)) from exc


class PassengerDetailForm(BootstrapFormMixin, forms.Form):
    full_name = forms.CharField(label=_("Full name"), max_length=150)
    age = forms.IntegerField(label=_("Age"), required=False, min_value=0, max_value=120)
    gender = forms.ChoiceField(
        label=_("Gender"), choices=[("", "---------")] + list(BookingPassenger.Gender.choices), required=False
    )
    phone = forms.CharField(label=_("Phone"), max_length=16, required=False)
    id_document_number = forms.CharField(label=_("ID document number"), max_length=50, required=False)


PassengerDetailFormSet = forms.formset_factory(PassengerDetailForm, extra=0)


class PromoCodeForm(BootstrapFormMixin, forms.Form):
    code = forms.CharField(label=_("Promo code"), max_length=30, required=False)


class PaymentMethodForm(BootstrapFormMixin, forms.Form):
    provider = forms.ChoiceField(
        label=_("Payment method"),
        choices=[
            (Payment.Provider.COUNTER, _("Pay at counter")),
            (Payment.Provider.MOCK, _("Mock gateway (dev/test)")),
        ],
    )


class EmailAuthenticationForm(BootstrapFormMixin, forms.Form):
    email = forms.EmailField(label=_("Email"))
    password = forms.CharField(label=_("Password"), widget=forms.PasswordInput)


class RegisterForm(BootstrapFormMixin, forms.Form):
    email = forms.EmailField(label=_("Email"))
    full_name = forms.CharField(label=_("Full name"), max_length=150)
    password = forms.CharField(label=_("Password"), widget=forms.PasswordInput)
    password_confirm = forms.CharField(label=_("Confirm password"), widget=forms.PasswordInput)

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get("password")
        password_confirm = cleaned_data.get("password_confirm")
        if password and password_confirm and password != password_confirm:
            self.add_error("password_confirm", _("Passwords do not match."))
        return cleaned_data


class ForgotPasswordRequestForm(BootstrapFormMixin, forms.Form):
    email = forms.EmailField(label=_("Email"))


class ForgotPasswordConfirmForm(BootstrapFormMixin, forms.Form):
    email = forms.EmailField(widget=forms.HiddenInput)
    code = forms.CharField(label=_("Reset code"), max_length=12)
    new_password = forms.CharField(
        label=_("New password"), widget=forms.PasswordInput, validators=[validate_password]
    )
    new_password_confirm = forms.CharField(label=_("Confirm new password"), widget=forms.PasswordInput)

    def clean(self):
        cleaned_data = super().clean()
        new_password = cleaned_data.get("new_password")
        new_password_confirm = cleaned_data.get("new_password_confirm")
        if new_password and new_password_confirm and new_password != new_password_confirm:
            self.add_error("new_password_confirm", _("Passwords do not match."))
        return cleaned_data


class GuestBookingLookupForm(BootstrapFormMixin, forms.Form):
    pnr = forms.CharField(label=_("PNR"), max_length=8)
    phone = forms.CharField(label=_("Phone"), max_length=16)

    def clean_phone(self):
        raw = self.cleaned_data["phone"]
        try:
            return normalize_phone(raw)
        except InvalidPhoneNumberError as exc:
            raise forms.ValidationError(str(exc)) from exc


class CancelBookingForm(BootstrapFormMixin, forms.Form):
    reason = forms.CharField(
        label=_("Reason (optional)"), required=False, widget=forms.Textarea(attrs={"rows": 2})
    )


class RescheduleDateForm(BootstrapFormMixin, forms.Form):
    date = forms.DateField(label=_("New date"), widget=forms.DateInput(attrs={"type": "date"}))


class ProfileForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = ["full_name", "phone", "preferred_language", "preferred_currency"]


class SavedPassengerForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = SavedPassenger
        fields = ["full_name", "age", "gender", "phone", "id_document_number"]
