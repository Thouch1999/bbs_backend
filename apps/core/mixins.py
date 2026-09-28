from apps.core.permissions import IsApprovedOperatorForWrites, IsOperatorOwnerOrManager


class OperatorScopedViewSetMixin:
    """
    An operator must only ever see and modify its own records. Enforced here
    once, in the queryset, rather than repeated in every operator-facing
    view — SRS 7.5-7.8. Assumes the model has an `operator` FK; set
    `operator_lookup` for an indirect relation (e.g. "route__operator").

    Writes additionally need an admin-approved operator (IsApprovedOperator's
    docstring always intended this for fleet/routes/schedule; until Step 15
    the mixin never applied it, so a still-pending operator could publish
    trips straight into public search).
    """

    permission_classes = [IsOperatorOwnerOrManager, IsApprovedOperatorForWrites]
    operator_lookup = "operator"

    def _operator(self):
        return self.request.user.operator_staff.operator

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.filter(**{f"{self.operator_lookup}_id": self._operator().id})

    def perform_create(self, serializer):
        if self.operator_lookup == "operator":
            serializer.save(operator=self._operator())
        else:
            serializer.save()
