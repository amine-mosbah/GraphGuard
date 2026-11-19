"""Detector suite — every detector returns explained signals, never bare scores."""

from graphguard.detection.abnormal_subgraph import (
    SimilarityClusterDetector,
    build_abnormal_subgraph_detector,
)
from graphguard.detection.base import Detector, Reason, Signal
from graphguard.detection.community_drift import (
    CommunityDriftDetector,
    build_community_drift_detector,
)
from graphguard.detection.node_outlier import (
    DominantOutlierDetector,
    EgonetOutlierDetector,
    build_node_outlier_detector,
)

__all__ = [
    "CommunityDriftDetector",
    "Detector",
    "DominantOutlierDetector",
    "EgonetOutlierDetector",
    "Reason",
    "Signal",
    "SimilarityClusterDetector",
    "build_abnormal_subgraph_detector",
    "build_community_drift_detector",
    "build_node_outlier_detector",
]
