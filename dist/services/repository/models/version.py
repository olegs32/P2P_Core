"""
Version management for artifacts
"""
import re
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class SemanticVersion:
    """Semantic version (major.minor.patch)"""
    major: int
    minor: int
    patch: int
    prerelease: Optional[str] = None  # alpha, beta, rc.1, etc
    build: Optional[str] = None  # build metadata

    @staticmethod
    def parse(version_str: str) -> 'SemanticVersion':
        """Parse version string"""
        # Pattern: major.minor.patch[-prerelease][+build]
        pattern = r'^(\d+)\.(\d+)\.(\d+)(?:-([a-zA-Z0-9.]+))?(?:\+([a-zA-Z0-9.]+))?$'
        match = re.match(pattern, version_str)

        if not match:
            raise ValueError(f"Invalid semantic version: {version_str}")

        major, minor, patch, prerelease, build = match.groups()

        return SemanticVersion(
            major=int(major),
            minor=int(minor),
            patch=int(patch),
            prerelease=prerelease,
            build=build
        )

    def __str__(self) -> str:
        """Convert to string"""
        version = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            version += f"-{self.prerelease}"
        if self.build:
            version += f"+{self.build}"
        return version

    def __lt__(self, other: 'SemanticVersion') -> bool:
        """Less than comparison"""
        # Compare major.minor.patch
        self_tuple = (self.major, self.minor, self.patch)
        other_tuple = (other.major, other.minor, other.patch)

        if self_tuple != other_tuple:
            return self_tuple < other_tuple

        # If base versions are equal, check prerelease
        # No prerelease > prerelease (1.0.0 > 1.0.0-beta)
        if self.prerelease is None and other.prerelease is not None:
            return False
        if self.prerelease is not None and other.prerelease is None:
            return True

        # Both have prerelease, compare lexicographically
        if self.prerelease and other.prerelease:
            return self.prerelease < other.prerelease

        return False

    def __le__(self, other: 'SemanticVersion') -> bool:
        return self < other or self == other

    def __gt__(self, other: 'SemanticVersion') -> bool:
        return not self <= other

    def __ge__(self, other: 'SemanticVersion') -> bool:
        return not self < other

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SemanticVersion):
            return False
        return (
            self.major == other.major and
            self.minor == other.minor and
            self.patch == other.patch and
            self.prerelease == other.prerelease
        )

    def is_compatible_with(self, other: 'SemanticVersion') -> bool:
        """Check if this version is compatible with another (same major version)"""
        return self.major == other.major

    def is_upgrade_from(self, other: 'SemanticVersion') -> bool:
        """Check if this is an upgrade from another version"""
        return self > other and self.is_compatible_with(other)


class VersionTag:
    """Version tag (stable, beta, lts, etc)"""
    STABLE = "stable"
    BETA = "beta"
    ALPHA = "alpha"
    LTS = "lts"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"
    RECOMMENDED = "recommended"
    SECURITY_FIX = "security-fix"
    HOTFIX = "hotfix"

    @staticmethod
    def is_valid(tag: str) -> bool:
        """Check if tag is valid"""
        valid_tags = [
            VersionTag.STABLE, VersionTag.BETA, VersionTag.ALPHA,
            VersionTag.LTS, VersionTag.DEPRECATED, VersionTag.ARCHIVED,
            VersionTag.RECOMMENDED, VersionTag.SECURITY_FIX, VersionTag.HOTFIX
        ]
        return tag in valid_tags


class VersionComparator:
    """Compare and validate versions"""

    @staticmethod
    def compare(v1: str, v2: str) -> int:
        """Compare two versions. Returns -1 if v1 < v2, 0 if equal, 1 if v1 > v2"""
        try:
            ver1 = SemanticVersion.parse(v1)
            ver2 = SemanticVersion.parse(v2)

            if ver1 < ver2:
                return -1
            elif ver1 > ver2:
                return 1
            else:
                return 0
        except ValueError:
            # Fallback to string comparison
            if v1 < v2:
                return -1
            elif v1 > v2:
                return 1
            else:
                return 0

    @staticmethod
    def is_newer(current: str, candidate: str) -> bool:
        """Check if candidate is newer than current"""
        return VersionComparator.compare(candidate, current) > 0

    @staticmethod
    def sort_versions(versions: list) -> list:
        """Sort versions from oldest to newest"""
        try:
            parsed = [(v, SemanticVersion.parse(v)) for v in versions]
            sorted_versions = sorted(parsed, key=lambda x: x[1])
            return [v[0] for v in sorted_versions]
        except ValueError:
            # Fallback to string sort
            return sorted(versions)

    @staticmethod
    def get_latest(versions: list) -> Optional[str]:
        """Get latest version from list"""
        if not versions:
            return None
        sorted_versions = VersionComparator.sort_versions(versions)
        return sorted_versions[-1]
