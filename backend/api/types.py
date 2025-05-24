from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from typing import Annotated, TypeAlias

	Secret: TypeAlias = Annotated[str, "credential"]  # noqa: UP040
