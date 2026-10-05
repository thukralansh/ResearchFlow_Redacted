"""Small HTTP client for the replaceable Streamlit frontend."""

from collections.abc import Mapping
from typing import Any, BinaryIO

import httpx


class ApiClientError(RuntimeError):
    """Safe frontend-facing backend failure."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_safe: bool = False,
        connection_failed: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_safe = retry_safe
        self.connection_failed = connection_failed


class ResearchFlowApiClient:
    def __init__(self, base_url: str, *, timeout_seconds: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    def list_projects(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/projects")
        return list(payload)

    def create_workflow(self, project_id: str, payload: Mapping[str, object]) -> dict[str, Any]:
        return dict(self._request("POST", f"/projects/{project_id}/workflows", json=dict(payload)))

    def get_workflow(self, project_id: str, workflow_run_id: str) -> dict[str, Any]:
        return dict(self._request("GET", f"/projects/{project_id}/workflows/{workflow_run_id}"))

    def list_workflow_events(self, project_id: str, workflow_run_id: str) -> list[dict[str, Any]]:
        return list(
            self._request("GET", f"/projects/{project_id}/workflows/{workflow_run_id}/events")
        )

    def list_workflow_interrupts(
        self, project_id: str, workflow_run_id: str
    ) -> list[dict[str, Any]]:
        return list(
            self._request("GET", f"/projects/{project_id}/workflows/{workflow_run_id}/interrupts")
        )

    def cancel_workflow(
        self, project_id: str, workflow_run_id: str, *, expected_lock_version: int
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/workflows/{workflow_run_id}/cancel",
                json={"expected_lock_version": expected_lock_version},
            )
        )

    def retry_workflow(
        self, project_id: str, workflow_run_id: str, *, expected_lock_version: int
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/workflows/{workflow_run_id}/retry",
                json={"expected_lock_version": expected_lock_version},
            )
        )

    def resolve_workflow_interrupt(
        self,
        project_id: str,
        workflow_run_id: str,
        interrupt_id: str,
        payload: Mapping[str, object],
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/workflows/{workflow_run_id}/interrupts/"
                f"{interrupt_id}/resolve",
                json=dict(payload),
            )
        )

    def resume_workflow(
        self, project_id: str, workflow_run_id: str, payload: Mapping[str, object]
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/workflows/{workflow_run_id}/resume",
                json=dict(payload),
            )
        )

    def get_project(self, project_id: str) -> dict[str, Any]:
        return dict(self._request("GET", f"/projects/{project_id}"))

    def get_artifact_freshness(
        self, project_id: str, artifact_type: str, artifact_id: str
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "GET",
                f"/projects/{project_id}/staleness/current",
                params={"artifact_type": artifact_type, "artifact_id": artifact_id},
            )
        )

    def list_blueprint_eligibility(self, project_id: str) -> list[dict[str, Any]]:
        return list(self._request("GET", f"/projects/{project_id}/blueprint-eligibility"))

    def get_blueprint_eligibility(self, project_id: str, assessment_id: str) -> dict[str, Any]:
        return dict(
            self._request("GET", f"/projects/{project_id}/blueprint-eligibility/{assessment_id}")
        )

    def assess_blueprint_eligibility(
        self, project_id: str, payload: Mapping[str, object]
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST", f"/projects/{project_id}/blueprint-eligibility/assess", json=dict(payload)
            )
        )

    def list_blueprints(self, project_id: str) -> list[dict[str, Any]]:
        return list(self._request("GET", f"/projects/{project_id}/blueprints"))

    def get_blueprint(self, project_id: str, blueprint_id: str) -> dict[str, Any]:
        return dict(self._request("GET", f"/projects/{project_id}/blueprints/{blueprint_id}"))

    def accept_blueprint(
        self, project_id: str, blueprint_id: str, payload: Mapping[str, object]
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/blueprints/{blueprint_id}/accept",
                json=dict(payload),
            )
        )

    def revise_blueprint(
        self, project_id: str, blueprint_id: str, payload: Mapping[str, object]
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/blueprints/{blueprint_id}/revisions",
                json=dict(payload),
                timeout_seconds=190.0,
            )
        )

    def get_implementation_plan(self, project_id: str, blueprint_id: str) -> dict[str, Any]:
        return dict(
            self._request("GET", f"/projects/{project_id}/verification/blueprints/{blueprint_id}")
        )

    def generate_implementation_plan(
        self, project_id: str, blueprint_id: str, payload: Mapping[str, object]
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/verification/blueprints/{blueprint_id}/generate",
                json=dict(payload),
                timeout_seconds=190.0,
            )
        )

    def revise_implementation_step(
        self, project_id: str, step_id: str, payload: Mapping[str, object]
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/verification/steps/{step_id}/revisions",
                json=dict(payload),
            )
        )

    def record_verification_result(
        self, project_id: str, check_id: str, payload: Mapping[str, object]
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/verification/checks/{check_id}/results",
                json=dict(payload),
            )
        )

    def create_project(
        self,
        payload: Mapping[str, object],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                "/projects",
                json=dict(payload),
                headers={"Idempotency-Key": idempotency_key},
            )
        )

    def update_project(self, project_id: str, payload: Mapping[str, object]) -> dict[str, Any]:
        return dict(self._request("PATCH", f"/projects/{project_id}", json=dict(payload)))

    def refine_question(
        self,
        project_id: str,
        question_id: str,
        payload: Mapping[str, object],
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/questions/{question_id}/refinements",
                json=dict(payload),
            )
        )

    def create_subquestion(self, project_id: str, payload: Mapping[str, object]) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/questions",
                json=dict(payload),
            )
        )

    def change_status(
        self,
        project_id: str,
        question_id: str,
        payload: Mapping[str, object],
        *,
        reopen: bool = False,
    ) -> dict[str, Any]:
        suffix = "reopen" if reopen else "inquiry-status"
        method = "POST" if reopen else "PATCH"
        return dict(
            self._request(
                method,
                f"/projects/{project_id}/questions/{question_id}/{suffix}",
                json=dict(payload),
            )
        )

    def list_sources(self, project_id: str) -> list[dict[str, Any]]:
        return list(self._request("GET", f"/projects/{project_id}/sources"))

    def get_source(self, project_id: str, source_id: str) -> dict[str, Any]:
        return dict(self._request("GET", f"/projects/{project_id}/sources/{source_id}"))

    def upload_primary_paper(
        self,
        project_id: str,
        *,
        file: BinaryIO,
        filename: str,
        content_type: str,
        form: Mapping[str, str],
        idempotency_key: str,
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/sources/primary-paper",
                data=form,
                files={"file": (filename, file, content_type)},
                headers={"Idempotency-Key": idempotency_key},
            )
        )

    def process_source(
        self,
        project_id: str,
        source_id: str,
        payload: Mapping[str, object],
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/sources/{source_id}/processing",
                json=dict(payload),
            )
        )

    def execute_extraction(
        self,
        project_id: str,
        payload: Mapping[str, object],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/extractions/execute",
                json=dict(payload),
                headers={"Idempotency-Key": idempotency_key},
                timeout_seconds=190.0,
            )
        )

    def list_source_processing_runs(self, project_id: str, source_id: str) -> list[dict[str, Any]]:
        return list(self._request("GET", f"/projects/{project_id}/sources/{source_id}/processing"))

    def attach_repository(
        self,
        project_id: str,
        payload: Mapping[str, object],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/repositories",
                json=dict(payload),
                headers={"Idempotency-Key": idempotency_key},
            )
        )

    def list_repository_history(self, project_id: str) -> list[dict[str, Any]]:
        return list(self._request("GET", f"/projects/{project_id}/repositories"))

    def discover_repository(self, project_id: str, payload: Mapping[str, object]) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/repositories/discovery",
                json=dict(payload),
            )
        )

    def list_repository_discoveries(self, project_id: str) -> list[dict[str, Any]]:
        return list(self._request("GET", f"/projects/{project_id}/repositories/discovery"))

    def get_repository_discovery(self, project_id: str, run_id: str) -> dict[str, Any]:
        return dict(self._request("GET", f"/projects/{project_id}/repositories/discovery/{run_id}"))

    def inspect_repository(self, project_id: str, payload: Mapping[str, object]) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/repositories/inspection",
                json=dict(payload),
            )
        )

    def list_repository_inspections(self, project_id: str) -> list[dict[str, Any]]:
        return list(self._request("GET", f"/projects/{project_id}/repositories/inspection"))

    def get_repository_inspection(self, project_id: str, run_id: str) -> dict[str, Any]:
        return dict(
            self._request("GET", f"/projects/{project_id}/repositories/inspection/{run_id}")
        )

    def list_mappings(self, project_id: str) -> list[dict[str, Any]]:
        return list(self._request("GET", f"/projects/{project_id}/mappings"))

    def review_mapping(
        self,
        project_id: str,
        mapping_id: str,
        action: str,
        payload: Mapping[str, object],
        *,
        request_id: str,
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/mappings/{mapping_id}/{action}",
                json=dict(payload),
                headers={"X-Request-ID": request_id},
            )
        )

    def derive_mapping_knowledge(
        self,
        project_id: str,
        payload: Mapping[str, object],
        *,
        request_id: str,
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/mapping-review/derive",
                json=dict(payload),
                headers={"X-Request-ID": request_id},
            )
        )

    def get_mapping_derivation(self, project_id: str, generation_record_id: str) -> dict[str, Any]:
        return dict(
            self._request(
                "GET",
                f"/projects/{project_id}/mapping-review/derivations/{generation_record_id}",
            )
        )

    def complete_mapping_review_gate(
        self,
        project_id: str,
        payload: Mapping[str, object],
        *,
        request_id: str,
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/mapping-review/complete",
                json=dict(payload),
                headers={"X-Request-ID": request_id},
            )
        )

    def list_knowledge(
        self,
        project_id: str,
        *,
        review_status: str | None = None,
        object_type: str | None = None,
    ) -> list[dict[str, Any]]:
        query = {
            key: value
            for key, value in {
                "review_status": review_status,
                "object_type": object_type,
            }.items()
            if value is not None
        }
        return list(self._request("GET", f"/projects/{project_id}/knowledge", params=query or None))

    def list_accepted_current_knowledge(self, project_id: str) -> list[dict[str, Any]]:
        return list(self._request("GET", f"/projects/{project_id}/knowledge/accepted-current"))

    def get_knowledge(self, project_id: str, object_id: str) -> dict[str, Any]:
        return dict(self._request("GET", f"/projects/{project_id}/knowledge/{object_id}"))

    def review_knowledge(
        self,
        project_id: str,
        object_id: str,
        action: str,
        payload: Mapping[str, object],
        *,
        request_id: str,
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/knowledge/{object_id}/{action}",
                json=dict(payload),
                headers={"X-Request-ID": request_id},
            )
        )

    def review_evidence(
        self,
        project_id: str,
        object_id: str,
        evidence_link_id: str,
        action: str,
        payload: Mapping[str, object],
        *,
        request_id: str,
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/knowledge/{object_id}/evidence/"
                f"{evidence_link_id}/{action}",
                json=dict(payload),
                headers={"X-Request-ID": request_id},
            )
        )

    def create_semantic_revision(
        self,
        project_id: str,
        object_id: str,
        payload: Mapping[str, object],
        *,
        request_id: str,
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/knowledge/{object_id}/semantic-revisions",
                json=dict(payload),
                headers={"X-Request-ID": request_id},
            )
        )

    def create_presentation_revision(
        self,
        project_id: str,
        object_id: str,
        payload: Mapping[str, object],
        *,
        request_id: str,
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/knowledge/{object_id}/presentation-revisions",
                json=dict(payload),
                headers={"X-Request-ID": request_id},
            )
        )

    def accept_knowledge_revision(
        self,
        project_id: str,
        predecessor_id: str,
        payload: Mapping[str, object],
        *,
        request_id: str,
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/knowledge/{predecessor_id}/accept-revision",
                json=dict(payload),
                headers={"X-Request-ID": request_id},
            )
        )

    def list_method_models(
        self, project_id: str, research_question_id: str
    ) -> list[dict[str, Any]]:
        return list(
            self._request(
                "GET",
                f"/projects/{project_id}/method-models/review-candidates",
                params={"research_question_id": research_question_id},
            )
        )

    def get_method_review(self, project_id: str, method_model_id: str) -> dict[str, Any]:
        return dict(
            self._request("GET", f"/projects/{project_id}/method-models/{method_model_id}/review")
        )

    def review_method_element(
        self,
        project_id: str,
        method_model_id: str,
        element_id: str,
        action: str,
        payload: Mapping[str, object],
        *,
        request_id: str,
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/method-models/{method_model_id}/elements/"
                f"{element_id}/{action}",
                json=dict(payload),
                headers={"X-Request-ID": request_id},
            )
        )

    def review_method_evidence(
        self,
        project_id: str,
        method_model_id: str,
        element_id: str,
        evidence_link_id: str,
        action: str,
        payload: Mapping[str, object],
        *,
        request_id: str,
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/method-models/{method_model_id}/elements/"
                f"{element_id}/evidence/{evidence_link_id}/{action}",
                json=dict(payload),
                headers={"X-Request-ID": request_id},
            )
        )

    def revise_method_element(
        self,
        project_id: str,
        method_model_id: str,
        element_id: str,
        payload: Mapping[str, object],
        *,
        request_id: str,
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/method-models/{method_model_id}/elements/"
                f"{element_id}/semantic-revisions",
                json=dict(payload),
                headers={"X-Request-ID": request_id},
            )
        )

    def exclude_method_element(
        self,
        project_id: str,
        method_model_id: str,
        element_id: str,
        payload: Mapping[str, object],
        *,
        request_id: str,
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/method-models/{method_model_id}/elements/"
                f"{element_id}/exclude",
                json=dict(payload),
                headers={"X-Request-ID": request_id},
            )
        )

    def review_method_section(
        self,
        project_id: str,
        method_model_id: str,
        review_section: str,
        payload: Mapping[str, object],
        *,
        request_id: str,
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/method-models/{method_model_id}/sections/"
                f"{review_section}/reviews",
                json=dict(payload),
                headers={"X-Request-ID": request_id},
            )
        )

    def accept_method_model(
        self,
        project_id: str,
        method_model_id: str,
        payload: Mapping[str, object],
        *,
        request_id: str,
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/method-models/{method_model_id}/accept",
                json=dict(payload),
                headers={"X-Request-ID": request_id},
            )
        )

    def get_primary_source_review_gate(
        self, project_id: str, method_model_id: str
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "GET",
                f"/projects/{project_id}/review-gates/primary-source",
                params={"method_model_id": method_model_id},
            )
        )

    def reassess_method_model(
        self,
        project_id: str,
        payload: Mapping[str, object],
        *,
        request_id: str,
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/method-model-reassessments",
                json=dict(payload),
                headers={"X-Request-ID": request_id},
            )
        )

    def get_method_model_reassessment(
        self, project_id: str, generation_record_id: str
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "GET",
                f"/projects/{project_id}/method-model-reassessments/{generation_record_id}",
            )
        )

    def reassess_mental_model(
        self,
        project_id: str,
        payload: Mapping[str, object],
        *,
        request_id: str,
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/mental-model-assessments",
                json=dict(payload),
                headers={"X-Request-ID": request_id},
            )
        )

    def get_current_mental_model_assessment_coverage(self, project_id: str) -> dict[str, Any]:
        return dict(
            self._request("GET", f"/projects/{project_id}/mental-model-assessments/current")
        )

    def get_mental_model_reassessment(
        self, project_id: str, generation_record_id: str
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "GET",
                f"/projects/{project_id}/mental-model-assessments/{generation_record_id}",
            )
        )

    def get_current_mental_model(self, project_id: str) -> dict[str, Any] | None:
        payload = self._request("GET", f"/projects/{project_id}/mental-models/current")
        return None if payload is None else dict(payload)

    def list_mental_models(self, project_id: str) -> list[dict[str, Any]]:
        return list(self._request("GET", f"/projects/{project_id}/mental-models/history"))

    def get_mental_model(self, project_id: str, mental_model_id: str) -> dict[str, Any]:
        return dict(self._request("GET", f"/projects/{project_id}/mental-models/{mental_model_id}"))

    def generate_mental_model(
        self,
        project_id: str,
        payload: Mapping[str, object],
        *,
        request_id: str,
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/mental-models/generate",
                json=dict(payload),
                headers={"X-Request-ID": request_id},
            )
        )

    def refresh_mental_model_rendering(
        self,
        project_id: str,
        mental_model_id: str,
        payload: Mapping[str, object],
        *,
        request_id: str,
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/mental-models/{mental_model_id}/renderings",
                json=dict(payload),
                headers={"X-Request-ID": request_id},
            )
        )

    def list_judgment_requests(self, project_id: str) -> list[dict[str, Any]]:
        return list(self._request("GET", f"/projects/{project_id}/judgment-requests"))

    def get_judgment_request(self, project_id: str, request_id: str) -> dict[str, Any]:
        return dict(self._request("GET", f"/projects/{project_id}/judgment-requests/{request_id}"))

    def create_judgment_request(
        self,
        project_id: str,
        payload: Mapping[str, object],
        *,
        request_id: str,
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/judgment-requests",
                json=dict(payload),
                headers={"X-Request-ID": request_id},
            )
        )

    def resolve_judgment_request(
        self, project_id: str, request_id: str, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/judgment-requests/{request_id}/resolve",
                json=dict(payload),
            )
        )

    def list_human_judgments(self, project_id: str, request_id: str) -> list[dict[str, Any]]:
        return list(
            self._request(
                "GET",
                f"/projects/{project_id}/judgment-requests/{request_id}/judgments",
            )
        )

    def complete_primary_source_review(
        self,
        project_id: str,
        payload: Mapping[str, object],
        *,
        request_id: str,
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/projects/{project_id}/review-gates/primary-source/complete",
                json=dict(payload),
                headers={"X-Request-ID": request_id},
            )
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, object] | None = None,
        data: Mapping[str, str] | None = None,
        files: Mapping[str, tuple[str, BinaryIO, str]] | None = None,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> Any:
        try:
            response = httpx.request(
                method,
                f"{self._base_url}{path}",
                timeout=self._timeout if timeout_seconds is None else timeout_seconds,
                json=json,
                data=data,
                files=files,
                headers=headers,
                params=params,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as error:
            status_code = error.response.status_code
            message = {
                404: "The requested item is no longer available.",
                409: "The current state changed. Refresh before trying again.",
                422: "The request is incomplete or invalid.",
            }.get(status_code, "ResearchFlow rejected the request.")
            try:
                payload = error.response.json()
            except ValueError:
                payload = None
            if isinstance(payload, dict) and payload.get("code") == "config.invalid":
                message = (
                    "ResearchFlow API configuration is incomplete. Check the server configuration."
                )
            raise ApiClientError(
                message,
                status_code=status_code,
                retry_safe=status_code >= 500,
            ) from error
        except httpx.ConnectError as error:
            raise ApiClientError(
                "Cannot connect to the ResearchFlow API.", retry_safe=True, connection_failed=True
            ) from error
        except httpx.TimeoutException as error:
            raise ApiClientError(
                "The ResearchFlow API request timed out.", retry_safe=True
            ) from error
        except (httpx.HTTPError, ValueError) as error:
            raise ApiClientError(
                "ResearchFlow returned an invalid response.", retry_safe=True
            ) from error
