<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# Choose the Right Assessment

## Separate the Three Results

| Assessment | What You Use It For |
|---|---|
| OSPS Baseline | Assess versioned security requirements by maturity level. |
| Best Practices | Self-certify broader development practices with supporting evidence. |
| Scorecard | Inspect automated signals about repository and supply-chain practices. |

Baseline has Levels 1, 2, and 3. Best Practices has Passing, Silver, and Gold. Scorecard reports automated check results and a score. Completing one does not automatically complete the others. Read their [Baseline catalog](https://baseline.openssf.org/versions/2026-08-28), [Passing criteria](https://www.bestpractices.dev/en/criteria/0), and [Scorecard documentation](https://scorecard.dev/) separately.

## Start at a Level You Can Operate

My recommendation is to assess Baseline Level 1 first, close its gaps, and then assess the higher levels. Keep the Passing worksheet alongside it because many underlying practices overlap. For a multi-repository organization, assess each repository and the product-level relationships between them.

Treat Level 3 as an operating target, not an alternate questionnaire that produces a better-looking badge. As requirements become stronger, you need evidence about governance, review, risk assessment, and sustained maintenance as well as repository files. Follow the versioned applicability instructions and inherited requirements when making a level claim.

Private security repositories can support a public project's process. They do not satisfy a public-source requirement by being made visible to an internal assessor. Assess the public product and preserve the confidentiality of its security work.

## Record Exceptions Precisely

Use N/A only when the criterion permits it and the software's actual scope supports it. Explain why it does not apply. For a supported alternative or exception, document the threat, rationale, compensating measure, owner, and review date.

A scanner suppression is narrower than a claim that the product is secure. An accepted advisory may depend on a specific configuration or unreachable code path; re-evaluate it when dependencies, features, or defaults change.

## Use the Checklist as a Work Queue

Copy the organization checklist for a repository, fill in its identity, and assign each gap. Link existing work rather than creating duplicate issues. Keep safe implementation tasks on the public roadmap and sensitive findings in the private security process.

A completed row needs enough information for another maintainer to reproduce the assessment. “See CI” is weak evidence when the reader cannot identify the source revision, workflow, test scope, or result.
