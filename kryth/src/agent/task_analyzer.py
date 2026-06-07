"""Task Analyzer — decomposes user requests into high-level work components.

This module identifies the minimal number of execution units needed to
complete a task. It groups related work into the same component and only
creates separate components for truly independent work areas.

Principles:
- Prefer fewer components (main agent handles as much as possible)
- Only split when work areas are independent and can run in parallel
- Never create one component per file/feature automatically
- Merge related sub-tasks into a single component
- Use dependencies to order components when needed
"""

from __future__ import annotations

import re
import math
from dataclasses import dataclass
from typing import List, Dict


@dataclass
class WorkComponent:
    """A high-level work unit that can be assigned to one agent."""
    id: str
    name: str
    description: str
    estimated_turns: int
    dependencies: List[str] = None  # component IDs
    
    def __post_init__(self):
        if self.dependencies is None:
            self.dependencies = []


@dataclass
class TaskAnalysis:
    """Result of analyzing a user request."""
    components: List[WorkComponent]
    has_dependencies: bool
    can_parallelize: bool  # true if components are independent
    reasoning: str
    estimated_total_turns: int = 0
    needs_multiple_agents: bool = False


class TaskAnalyzer:
    """Analyzes user requests to determine high-level work components."""
    
    # High-level component patterns (order matters: more specific first)
    _COMPONENT_PATTERNS = [
        # Automation / browser tasks (single agent)
        (r'\b(automate|browser|scrape|crawl|selenium|puppeteer|playwright)\b', 'automation', 120),
        
        # Machine learning / AI
        (r'\b(ml|machine\s+learning|ai|neural|deep\s+learning|model\s+training|inference\s+server)\b', 'ml', 150),
        
        # Frontend / UI
        (r'\b(frontend|ui|user\s+interface|client|react|vue|angular|svelte|next\.js|nuxt|web\s+app|landing\s+page|dashboard|admin\s+panel)\b', 'frontend', 100),
        
        # Backend / API
        (r'\b(backend|server|api|rest|graphql|endpoints|microservice|service)\b', 'backend', 120),
        
        # Scripts / CLI
        (r'\b(scripts?|utilities?|tools?|cli|command\s+line|argparse|click)\b', 'scripts', 60),
        
        # Tests (usually separate phase)
        (r'\b(tests?|testing|pytest|unittest|spec|test\s+suite|e2e|integration\s+test)\b', 'tests', 40),
        
        # Documentation
        (r'\b(docs|documentation|readme|guide|tutorial|api\s+doc)\b', 'docs', 30),
    ]
    
    # Simple task indicators (single agent, no decomposition)
    _SIMPLE_INDICATORS = [
        r'^(fix|debug|explain|what|why|how|show|list|print|check|review|'
        r'refactor|rename|move|delete|remove|help|tell|describe|summarize|'
        r'read|open|find|search|get|run|test|verify|format|lint)\b',
        r'^\w+\s+(?:in|to|the)\s+(?:file|function|class|method)\s+\w+',
        r'^(add|remove|update)\s+(?:a\s+)?(?:field|property|method)\s+to\s+\w+',
        r'^fix\b',
        r'^update\b',
    ]
    
    def analyze(self, user_input: str) -> TaskAnalysis:
        """Analyze a user request and return high-level work components."""
        text = user_input.lower().strip()
        
        # Simple tasks: single agent, no decomposition
        if self._is_simple_task(text):
            return TaskAnalysis(
                components=[WorkComponent(
                    id="main",
                    name="Complete Task",
                    description=user_input,
                    estimated_turns=120
                )],
                has_dependencies=False,
                can_parallelize=False,
                reasoning="Simple task - single agent sufficient",
                estimated_total_turns=120,
                needs_multiple_agents=False
            )
        
        # Detect high-level components
        detected = self._detect_components(text)
        
        # If no components detected, fallback to single agent
        if not detected:
            return TaskAnalysis(
                components=[WorkComponent(
                    id="main",
                    name="Main Work",
                    description=user_input,
                    estimated_turns=180
                )],
                has_dependencies=False,
                can_parallelize=False,
                reasoning="No specific components identified - using single agent",
                estimated_total_turns=180,
                needs_multiple_agents=False
            )
        
        # Build WorkComponent objects
        components = []
        for comp_id, (name, base_turns) in detected.items():
            # Adjust turns based on quantity if mentioned
            turns = self._adjust_turns_by_quantity(text, comp_id, base_turns)
            components.append(WorkComponent(
                id=comp_id,
                name=name,
                description=f"Build {name.lower()}",
                estimated_turns=turns
            ))
        
        # Set dependencies: tests and docs depend on everything else
        components = self._set_dependencies(components)
        
        # Determine if parallelization is possible
        has_deps = any(c.dependencies for c in components)
        # Parallel possible if no dependencies among non-test/doc components
        non_aux = [c for c in components if c.id not in ['tests', 'docs']]
        can_parallel = not has_deps and len(non_aux) > 1
        
        total_turns = sum(c.estimated_turns for c in components)
        
        reasoning = f"Components: {len(components)} ({', '.join(c.id for c in components)}). "
        if has_deps:
            reasoning += "Dependencies present - sequential. "
        elif can_parallel:
            reasoning += "Independent - parallel possible. "
        else:
            reasoning += "Single component effectively."
        
        return TaskAnalysis(
            components=components,
            has_dependencies=has_deps,
            can_parallelize=can_parallel,
            reasoning=reasoning,
            estimated_total_turns=total_turns,
            needs_multiple_agents=len(components) > 1
        )
    
    def _is_simple_task(self, text: str) -> bool:
        """Check if this is a simple single-agent task."""
        for pattern in self._SIMPLE_INDICATORS:
            if re.match(pattern, text, re.I):
                return True
        # Single file/function modifications
        if re.search(r'\b(file|function|class|method)\s+\w+', text, re.I):
            return True
        # "Fix X" or "Update Y" type tasks
        if re.match(r'^(fix|update|change|modify)\s+\w+', text, re.I):
            return True
        return False
    
    def _detect_components(self, text: str) -> Dict[str, tuple]:
        """Detect high-level components. Returns dict: id -> (name, base_turns)."""
        detected = {}
        for pattern, comp_id, base_turns in self._COMPONENT_PATTERNS:
            if re.search(pattern, text, re.I):
                # Avoid duplicates: if already detected a more specific, skip
                # But patterns are ordered; we want to allow multiple if they are truly separate
                # However, some components subsume others. We'll apply merging rules after.
                detected[comp_id] = (comp_id.title(), base_turns)
        
        # Merging rules: if we have both backend and database/auth, keep backend only
        # (database and auth are part of backend)
        if 'backend' in detected:
            # Remove database, auth if present as separate (they're not in our patterns, but just in case)
            for sub in ['database', 'auth']:
                if sub in detected:
                    del detected[sub]
        
        # If we have frontend and admin, keep frontend (admin is part of frontend)
        if 'frontend' in detected and 'admin' in detected:
            del detected['admin']
        
        # If we have ml and data, keep ml (data pipeline is part of ml)
        if 'ml' in detected and 'data' in detected:
            del detected['data']
        
        return detected
    
    def _adjust_turns_by_quantity(self, text: str, comp_id: str, base_turns: int) -> int:
        """Adjust estimated turns based on quantity mentioned (e.g., '20 scripts')."""
        # Map component id to keywords to look for
        keywords = {
            'frontend': r'(pages?|components?|screens?|views?)',
            'backend': r'(endpoints?|routes?|controllers?|models?)',
            'scripts': r'(scripts?|files?)',
            'tests': r'(tests?|cases?)',
            'docs': r'(pages?|sections?)',
            'ml': r'(models?|pipelines?)',
            'automation': r'(steps?|actions?|tasks?)',
        }
        
        if comp_id not in keywords:
            return base_turns
        
        # Look for a number followed by the keyword or a generic "items"
        pattern = rf'(\d+)\s+(?:unrelated\s+)?(?:utility\s+)?(?:{keywords[comp_id]}|items?|things?)'
        matches = re.findall(pattern, text, re.I)
        if matches:
            try:
                quantity = int(matches[0])
                # Scale turns, but with diminishing returns (not linear)
                # E.g., 20 scripts might be 2x base, not 20x
                if quantity > 1:
                    # Use sqrt scaling to avoid explosion
                    scale = math.sqrt(quantity)
                    return int(base_turns * scale)
            except:
                pass
        
        return base_turns
    
    def _set_dependencies(self, components: List[WorkComponent]) -> List[WorkComponent]:
        """Set dependencies between components."""
        comp_map = {c.id: c for c in components}
        
        # Tests depend on all other components
        if 'tests' in comp_map:
            test_comp = comp_map['tests']
            test_comp.dependencies = [c.id for c in components if c.id != 'tests']
        
        # Documentation depends on all other components
        if 'docs' in comp_map:
            doc_comp = comp_map['docs']
            doc_comp.dependencies = [c.id for c in components if c.id != 'docs']
        
        # Automation is usually independent
        # Scripts are independent
        # Frontend and backend are independent (no deps)
        # ML is independent
        
        return components