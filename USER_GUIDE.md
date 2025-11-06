# Conversational Protein Search - User Guide

## What is This?

The Conversational Protein Search is an AI assistant that helps you find information about proteins in the UniProt database using natural, conversational language. Instead of learning complex search syntax, you can simply ask questions like "Find me information about insulin in humans" or "Show me proteins related to cancer."

## How It Works

The system understands your questions, searches the UniProt protein database, and guides you through finding exactly what you're looking for. It can:

- **Extract information** from your questions automatically
- **Ask for clarification** when needed
- **Suggest refinements** when there are too many results
- **Try alternate names** when searches don't find anything
- **Show detailed information** about the proteins you select

## Getting Started

### Starting a Search

Simply type your question about a protein. For example:

- "Find insulin"
- "Show me p53 in humans"
- "What proteins are related to EGFR?"

The system will ask for any missing information and guide you through the process.

### Ending a Conversation

Type `exit` to end your current conversation and start fresh. Your previous conversation history will be cleared, and you can begin a new search.

---

## The Search Process

### Step 1: Provide Basic Information

The system needs at least two pieces of information to search:
- **Protein name** (e.g., "insulin", "EGFR", "p53")
- **Organism** (e.g., "human", "mouse", "Homo sapiens")

**Example:**
- You: "insulin"
- System: "Please provide: organism (e.g., Homo sapiens, Mus musculus)"
- You: "human"
- System: *Searches and finds results*

### Step 2: Review Results

The system will tell you how many results were found:

- **0 results**: The system will try alternate protein names automatically
- **1-10 results**: You'll see a list to choose from
- **More than 10 results**: The system will suggest ways to narrow down your search

### Step 3: Refine Your Search (If Needed)

If there are too many results (>10), the system will suggest ways to narrow them down:

- **Gene symbols**: "Try adding the gene symbol INS"
- **Length ranges**: "Narrow down to proteins between 50-100 amino acids"
- **Specific features**: "Filter by location in the cell (nucleus, membrane, etc.)"
- **Keywords**: "Focus on specific protein types or functions"

**Example:**
- System: "Found 100 results. Here are three ways to narrow down:
  1. Narrow down the length range to 50-100 amino acids
  2. Add the gene symbol INS
  3. What type of insulin are you interested in?"
- You: "INS"
- System: *Refines search and shows fewer results*

### Step 4: Select a Protein

When you have 1-10 results, you'll see a numbered list:

```
Please select one protein (reply with number or accession):
1. P01308 - Insulin (Homo sapiens) | length: 110 aa
2. P06213 - Proinsulin (Homo sapiens) | length: 110 aa
3. P51460 - Insulin-like growth factor (Homo sapiens) | length: 195 aa
```

Simply reply with the number (e.g., "1") or the accession code (e.g., "P01308").

### Step 5: View Details

Once you select a protein, the system will show you detailed information:

```
Confirmed protein details:
Accession: P01308
Name: Insulin
Organism: Homo sapiens
Length: 110 aa
Mass: 12345 Da
Genes: INS
```

---

## Common Use Cases

### Finding a Specific Protein

**Scenario:** You want to find information about a specific protein in a specific organism.

**Example Conversation:**
1. You: "EGFR"
2. System: "Please provide: organism"
3. You: "human"
4. System: "Found 15 results. Here are ways to narrow down..."
5. You: "receptor"
6. System: "Found 3 results. Please select one:"
   - Lists 3 options
7. You: "1"
8. System: Shows detailed information about EGFR

### Searching with Refinements

**Scenario:** You want to find proteins with specific characteristics.

**Example Conversation:**
1. You: "Find proteins in the nucleus involved in cancer"
2. System: "Please provide: organism"
3. You: "human"
4. System: "Found 200 results. Try narrowing by:"
   - Specific protein names
   - Gene symbols
   - Disease associations
5. You: "p53"
6. System: "Found 5 results. Please select one:"
   - Lists p53-related proteins
7. You: "1"
8. System: Shows p53 protein details

### When Search Returns Nothing

**Scenario:** The protein name you use isn't found in the database.

**Example Conversation:**
1. You: "PDIb'a'-hLIF"
2. System: "Please provide: organism"
3. You: "human"
4. System: "Search → 0 hits"
5. System: "Trying alternate name: PDIbA"
6. System: "Search → 0 hits"
7. System: "Tried an alternate protein name but couldn't find results. Please provide a different protein name or more details."
8. You: "PDI"
9. System: *Finds results with standard name*

---

## What You Can Search For

### Protein Names
- Common names: "insulin", "hemoglobin", "collagen"
- Gene symbols: "INS", "HBB", "COL1A1"
- Scientific names: "Epidermal growth factor receptor"
- UniProt accession codes: "P01308", "P00533"

### Organisms
- Common names: "human", "mouse", "rat", "yeast"
- Scientific names: "Homo sapiens", "Mus musculus", "Saccharomyces cerevisiae"
- The system understands both formats

### Refinements (Additional Search Criteria)

You can include these in your search or add them during refinement:

- **Gene symbols**: "INS", "EGFR"
- **Length**: "50 amino acids", "between 100-200 aa", "less than 300 aa"
- **Location in cell**: "nucleus", "membrane", "cytoplasm", "mitochondria"
- **Function**: "receptor", "enzyme", "kinase", "ligand"
- **Post-translational modifications**: "phosphorylated", "glycosylated"
- **Disease associations**: "cancer", "Alzheimer's", "diabetes"
- **Protein domains**: "SH2 domain", "kinase domain"
- **Biological pathways**: "MAPK pathway", "apoptosis"

**Examples:**
- "insulin in humans, length 50-100 amino acids"
- "EGFR receptor in the membrane"
- "phosphorylated proteins in cancer"
- "p53 in nucleus, involved in apoptosis"

---

## Tips for Best Results

### Be Specific
- Include the organism when possible: "insulin human" instead of just "insulin"
- Use specific protein names when you know them
- Provide refinements upfront if you have specific criteria

### Use Refinements When Needed
- If you get too many results, the system will suggest refinements
- Follow the suggestions to narrow down effectively
- You can add multiple refinements in one message

### Try Alternate Names
- If a search returns nothing, the system will try alternate names automatically
- You can also try common alternative names yourself
- Gene symbols often work better than full names

### Examples of Good Queries

✅ **Good:**
- "insulin human"
- "EGFR receptor in human, in membrane"
- "p53 in nucleus, cancer related"
- "proteins less than 100 amino acids in humans"

❌ **Less Effective:**
- "protein" (too vague)
- "something related to diabetes" (not specific enough)
- "all proteins" (too broad)

---

## Understanding the Results

### Search Summary

When the system searches, you'll see a summary like:

```
Search → 15 hits | query: insulin AND organism_id:"9606" | filters: organism: Homo sapiens | top organism: Homo sapiens (15)
```

This tells you:
- **Number of results found**: 15 hits
- **Query used**: The search terms
- **Filters applied**: What refinements were used
- **Top organism**: Most common organism in results

### Result List

When you see a list of results, each entry shows:
- **Number**: For easy selection
- **Accession code**: Unique UniProt identifier (e.g., P01308)
- **Protein name**: Common or scientific name
- **Organism**: Which species it's from
- **Length**: Protein size in amino acids (if available)

### Protein Details

After selecting a protein, you'll see:
- **Accession**: UniProt identifier
- **Name**: Full protein name
- **Organism**: Scientific name
- **Length**: Size in amino acids
- **Mass**: Molecular weight in Daltons
- **Genes**: Associated gene symbols

---

## FAQ

### What is UniProt?
UniProt is a comprehensive database of protein information, including sequences, functions, and related data from various species.

### Why does it ask for organism?
The same protein name can exist in multiple species. Specifying the organism helps narrow down to the exact protein you're looking for.

### What if I don't know the exact protein name?
Start with what you know (e.g., "insulin" or "EGFR"). The system will guide you or try alternate names if the initial search doesn't work.

### Can I search for multiple proteins at once?
Currently, the system searches for one protein at a time. You can search for multiple proteins in separate conversations.

### What happens if I make a mistake?
You can always refine your search or start over. Type `exit` to end the current conversation and begin fresh.

### How do I refine my search?
The system will automatically suggest refinements when there are too many results. You can also add refinements in your initial query, like "insulin human, length 50-100 amino acids."

### What if the search returns nothing?
The system will automatically try alternate protein names. If that doesn't work, try:
- Using a gene symbol instead of the protein name
- Checking the spelling
- Using a more common name
- Providing more context or refinements

### Can I save my search results?
Currently, the system doesn't save results automatically. You can copy the protein details shown at the end of your search.

---

## Example Conversations

### Example 1: Simple Protein Search

```
You: insulin
System: Please provide: organism (e.g., Homo sapiens, Mus musculus)

You: human
System: Search → 100 hits | query: insulin AND organism_id:"9606" | filters: organism: Homo sapiens
        Found 100 results. Here are three ways to narrow down:
        1. Narrow down the length range to 50-100 amino acids
        2. Add the gene symbol INS
        3. What type of insulin are you interested in?

You: INS
System: Search → 3 hits | query: insulin AND organism_id:"9606" AND gene:INS
        Please select one protein (reply with number or accession):
        1. P01308 - Insulin (Homo sapiens) | length: 110 aa
        2. P06213 - Proinsulin (Homo sapiens) | length: 110 aa
        3. P51460 - Insulin-like growth factor (Homo sapiens) | length: 195 aa

You: 1
System: Confirmed protein details:
        Accession: P01308
        Name: Insulin
        Organism: Homo sapiens
        Length: 110 aa
        Mass: 12345 Da
        Genes: INS
```

### Example 2: Search with Refinements

```
You: Find proteins in the nucleus related to cancer
System: Please provide: organism

You: human
System: Search → 250 hits
        Here are three ways to narrow down:
        1. Specify a protein name or gene symbol (e.g., p53, TP53)
        2. Narrow by length range (current range: 50-5000 aa)
        3. Specify disease type or pathway

You: p53
System: Search → 8 hits
        Please select one protein (reply with number or accession):
        1. P04637 - Cellular tumor antigen p53 (Homo sapiens) | length: 393 aa
        2. P04638 - Isoform 2 of p53 (Homo sapiens) | length: 393 aa
        ...

You: 1
System: Confirmed protein details:
        Accession: P04637
        Name: Cellular tumor antigen p53
        Organism: Homo sapiens
        Length: 393 aa
        Mass: 43653 Da
        Genes: TP53
```

### Example 3: Search with No Results

```
You: XYZ123protein
System: Please provide: organism

You: human
System: Search → 0 hits
        Trying alternate name: XYZ123

System: Search → 0 hits
        Tried an alternate protein name but couldn't find results. 
        Please provide a different protein name or more details.

You: protein disulfide isomerase
System: Search → 15 hits
        Please select one protein...
```

---

## Getting Help

If you encounter issues:
1. Make sure you've provided both protein name and organism
2. Try using gene symbols instead of protein names
3. Be more specific with refinements if you get too many results
4. Try alternate names if you get no results
5. Type `exit` to start fresh if needed

---

## Glossary

- **Accession Code**: A unique identifier for each protein in UniProt (e.g., P01308)
- **Amino Acid (aa)**: The building blocks of proteins
- **Gene Symbol**: A short abbreviation for a gene (e.g., INS for insulin)
- **Organism**: The species the protein comes from (e.g., human, mouse)
- **Protein**: A molecule that performs functions in living organisms
- **Refinement**: Additional search criteria to narrow down results
- **UniProt**: Universal Protein database, a comprehensive protein information resource

---

*Last Updated: 2025*

