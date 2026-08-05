# NAS Portal Member Upload Template

## Field Summary and Completion Guide

### 1. Purpose of the Template

The **NAS HR Request Template** is a structured bulk-enrolment file used to upload employees and their dependants to the NAS medical insurance portal.

Each row in the **“Sample Template”** sheet represents **one individual member**. The file captures:

* Policy and benefit-category information
* Employee and dependant details
* Family and principal-member linkage
* Personal identification and regulatory information
* Work and residence location details
* Contact and employment information
* Visa, passport and supporting-document references
* Medical declarations and pre-existing-condition information

Supporting documents, such as photographs, medical declaration forms, Emirates IDs, passports, visas and birth certificates, are normally submitted separately in a ZIP file. Certain Excel fields contain the **exact attachment filename**, allowing NAS to match each document to the correct member.

---

## 2. General Completion Rules

1. Enter one member per row, beginning from **Row 2**.

2. Do not change, rename, delete or rearrange any column headers.

3. Do not change the existing cell format.

4. Dates must be entered as:

   * `DD-MM-YYYY`; or
   * `DD/MM/YYYY`

5. Where a dropdown is available, select the value from the dropdown. Do not manually type an alternative value.

6. Use the exact member information appearing on the Emirates ID, passport, visa or birth certificate.

7. The filename entered under **Member Photo** or **Medical Declaration File** must exactly match the filename included in the ZIP folder.

8. Members of the same family should have consistent:

   * Contract Name
   * Category
   * Family Number
   * Principal-member linkage

9. For dependants being added under an already insured employee, the employee’s existing **Principal Card Number** should be used to link the dependant to the correct principal member.

10. Blank cells should only be left where the field is genuinely not applicable.

---

# 3. Complete Field Dictionary

## A. Policy, Member and Family Information

| No. | Field                  | Purpose and Completion Requirement                                                                                                                                                                                                                  |
| --: | ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
|   1 | **Contract Name**      | The policy or contract under which the member is being enrolled. Select the exact contract from the dropdown. All members belonging to the same family should generally have the same Contract Name. This field is marked as a core required field. |
|   2 | **First Name**         | Member’s first name in English, exactly as shown on the official identification document. Core required field.                                                                                                                                      |
|   3 | **Middle Name**        | Member’s middle name in English. Complete where shown on the passport or Emirates ID. The column is currently hidden in the template.                                                                                                               |
|   4 | **Last Name**          | Member’s surname or family name in English, exactly as shown on the official identification document. Core required field.                                                                                                                          |
|   5 | **Arabic First Name**  | Member’s first name in Arabic. Complete using the official Arabic spelling where available. The column is currently hidden.                                                                                                                         |
|   6 | **Arabic Middle Name** | Member’s middle name in Arabic. Complete where applicable. The column is currently hidden.                                                                                                                                                          |
|   7 | **Arabic Last Name**   | Member’s surname or family name in Arabic. Complete where applicable. The column is currently hidden.                                                                                                                                               |
|   8 | **Effective Date**     | Date from which the member’s medical insurance cover should begin. The workbook describes this as the employee’s start date. Enter as `DD-MM-YYYY` or `DD/MM/YYYY`. Core required field.                                                            |
|   9 | **DOB**                | Member’s date of birth. Enter as `DD-MM-YYYY` or `DD/MM/YYYY`. Core required field.                                                                                                                                                                 |
|  10 | **Gender**             | Select **Male** or **Female** from the dropdown. Core required field.                                                                                                                                                                               |
|  11 | **Marital Status**     | Select Married, Divorced, Widowed or Single from the dropdown. Core required field.                                                                                                                                                                 |
|  12 | **Category**           | Medical insurance benefit category or plan class applicable to the member. Select the exact category from the dropdown. Core required field.                                                                                                        |
|  13 | **Relation**           | Relationship of the member to the insured employee or principal member. Available values include Principal, Spouse, Child, Parent, Ex-Spouse and Others. Core required field.                                                                       |
|  14 | **Department**         | Employee’s internal department or business unit. The column is hidden and points to a dropdown sheet; however, the Department lookup sheet is currently blank.                                                                                      |
|  15 | **Grade**              | Employee’s organisational or employment grade. The column is hidden and points to a dropdown sheet; however, the Grade lookup sheet is currently blank.                                                                                             |
|  16 | **Principal Card No.** | Existing NAS insurance card or member number of the principal employee. This is especially important when adding a spouse, child, parent or another dependant under an employee who is already insured. The column is currently hidden.             |
|  17 | **Family No.**         | Common family reference used to group the principal and dependants. Members belonging to the same family should use the same family reference where instructed.                                                                                     |
|  18 | **Staff ID**           | Employer-issued employee or staff number. This is the main internal tracking reference for identifying the employee and monitoring the request.                                                                                                     |

---

## B. Nationality, Identification and Location Information

| No. | Field                 | Purpose and Completion Requirement                                                                                                                                                                                                        |
| --: | --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
|  19 | **Nationality**       | Member’s nationality. Select from the nationality dropdown. The lookup list contains approximately 205 nationality values. Core required field.                                                                                           |
|  20 | **Sub-Nationality**   | Additional nationality classification, mainly used for UAE nationals. The UAE options identify the passport-issuing emirate and whether the member has Kholasat Qaid or a Marsoom/Decree. The column is hidden.                           |
|  21 | **Emirates ID**       | Member’s Emirates ID number. Enter exactly as appearing on the Emirates ID. The workbook marks this as a core required field, although operational exceptions may apply for newborns or members whose Emirates ID is still under process. |
|  22 | **Unified No**        | UAE Unified Identification Number, also referred to as UID. This is generally found on the visa, entry permit or immigration record and is used for regulatory identification.                                                            |
|  23 | **Passport No**       | Member’s passport number, entered exactly as shown on the passport.                                                                                                                                                                       |
|  24 | **Work Country**      | Country in which the member works. The workbook states that this is required for members under a Dubai category. Select from the country dropdown.                                                                                        |
|  25 | **Work Emirate**      | Emirate in which the member works. The workbook states that this is required for members under a Dubai category. Select from the seven-emirate dropdown.                                                                                  |
|  26 | **Work Region**       | More detailed work location or district. The available dropdown values depend on the Work Emirate selected. For example, Dubai provides Dubai-area options and Abu Dhabi provides Abu Dhabi/Al Ain-area options.                          |
|  27 | **Residence Country** | Member’s country of residence. The workbook states that this is required for members under a Dubai category.                                                                                                                              |
|  28 | **Residence Emirate** | Emirate in which the member resides. The workbook states that this is required for members under a Dubai category.                                                                                                                        |
|  29 | **Residence Region**  | Member’s residential area or district. The dropdown depends on the Residence Emirate selected.                                                                                                                                            |

---

## C. Contact, Employment, Visa and Document Information

| No. | Field                        | Purpose and Completion Requirement                                                                                                                                                                                                                                                                           |
| --: | ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
|  30 | **Email**                    | Member’s email address. The workbook specifically states that an email address is required for principal members. The column is visually marked as a core required field.                                                                                                                                    |
|  31 | **Mobile No**                | Member’s current mobile telephone number. Include the correct country code where required by the upload process.                                                                                                                                                                                             |
|  32 | **Salary Band**              | Member’s monthly salary classification. The workbook states that this is required for members under a Dubai category. Select one of the prescribed salary ranges.                                                                                                                                            |
|  33 | **Commission**               | Yes/No dropdown. The workbook does not explain what this indicator represents operationally. Its intended use should be confirmed with NAS or the relevant account handler before completion.                                                                                                                |
|  34 | **Visa Issuance Emirate**    | Place or status under which the member’s UAE visa or residency document was issued. The dropdown includes emirates as well as UAE National, GCC National, Diplomat and Dubai National classifications.                                                                                                       |
|  35 | **Birth Certificate Number** | Number appearing on the birth certificate. This is normally relevant for newborn or child additions where a birth certificate is used as the primary supporting document.                                                                                                                                    |
|  36 | **Visa File Number**         | Immigration or residence visa file number appearing on the UAE visa or residency record.                                                                                                                                                                                                                     |
|  37 | **Member Photo**             | Exact filename of the member photograph included in the accompanying ZIP folder. The entry must match the attachment filename, including the file extension.                                                                                                                                                 |
|  38 | **Member Type**              | Regulatory/residency classification of the member. The workbook states that this is required for members under a Dubai category. Select from the dropdown.                                                                                                                                                   |
|  39 | **Occupation**               | Member’s job title or occupation. Select from the prescribed occupation list, which contains approximately 3,483 values. The exact available NAS occupation wording should be used rather than entering a customised title.                                                                                  |
|  40 | **Regulator No**             | Regulatory membership or enrolment reference. The workbook does not provide a detailed definition of this field. The applicable number should be confirmed based on the relevant regulator, member type and jurisdiction.                                                                                    |
|  41 | **Passport Expiry Date**     | Expiry date of the member’s passport. The explanation sheet does not provide a separate instruction, but the same date format should be followed: `DD-MM-YYYY` or `DD/MM/YYYY`.                                                                                                                              |
|  42 | **Visa Expiry Date**         | Expiry date of the member’s UAE visa or residence permit. Use `DD-MM-YYYY` or `DD/MM/YYYY`.                                                                                                                                                                                                                  |
|  43 | **Visa Type**                | Type of UAE visa held by the member. The explanation sheet identifies the following expected values: Investor, Golden Visa, Employment, Dependants, Elderly Parents and Any Other Resident. This field does not currently have a dropdown control, so the prescribed wording should be entered consistently. |
|  44 | **Company Phone**            | Employer or sponsoring company’s telephone number.                                                                                                                                                                                                                                                           |
|  45 | **Company Mail**             | Employer or sponsoring company’s email address.                                                                                                                                                                                                                                                              |

---

## D. Continuity and Medical Declaration Information

| No. | Field                        | Purpose and Completion Requirement                                                                                                                                                                                                                                                                                                                   |
| --: | ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
|  46 | **COC Available**            | Indicates whether a Certificate of Continuity is available for the member. Enter **Yes** or **No**. The workbook states that a blank value will be treated as **No**. A COC is normally used to evidence the member’s previous medical insurance coverage and continuity.                                                                            |
|  47 | **PEC Declaration**          | Codes representing the member’s declared pre-existing or medical conditions. The codes must be selected from the **Declaration codes** sheet. Where several conditions apply, the workbook example shows the codes entered together, such as `CAN101, CIR102, DER104`.                                                                               |
|  48 | **Medical Declaration File** | Exact filename of the member’s completed medical declaration form included in the ZIP folder, for example `Ahmed MAF.pdf`. The filename entered in Excel must exactly match the uploaded attachment.                                                                                                                                                 |
|  49 | **Waived PEC Declaration**   | Declaration codes relating to conditions for which a waiver has been recorded or instructed. The workbook directs the user to use the same declaration-code list but does not clearly explain the distinction between this field and the standard PEC Declaration field. It should only be completed based on confirmed NAS or insurer instructions. |

---

# 4. Core Fields Visually Marked as Required

The workbook appears to use **red-filled headers** to identify its principal required fields:

* Contract Name
* First Name
* Last Name
* Effective Date
* DOB
* Gender
* Marital Status
* Category
* Relation
* Nationality
* Emirates ID
* Email

Some of these fields may still have operational exceptions. For example:

* Email is specifically stated as required for principal members.
* Emirates ID may not yet be available for a newborn.
* Certain Dubai-regulated fields are conditionally required even though their headers are not red.

---

# 5. Dropdown Values

## Contract Name

The template currently contains the following 12 contract options:

1. ALDAR ESTATES INVESTMENT - SOLE PROPRIETORSHIP L.L.C (FM) - AUH
2. BASATIN LANDSCAPING - SOLE PROPRIETOSHIP LLC_AUH
3. KHIDMAH - SOLE PROPRIETORSHIP L.L.C.
4. KHIDMAH - SOLE PROPRIETORSHIP L.L.C.-ADGM
5. INSPIRE INTEGRATED SERVICES LLC (DXB)
6. INSPIRE INTEGRATED FACILITIES MANAGEMENT LLC
7. 800TEK FACILITIES MANAGEMENT LLC
8. INSPIRE INTEGRATED SERVICES LLC (AUH)
9. ORIONTEK INNOVATIONS LLC
10. ESTATES CENTRALIZED SUPPORT SERVICES - L.L.C - O.P.C.
11. SPARK SECURITY SERVICES - SOLE PROPRIETORSHIP L.L.C.
12. HANSA ENERGY SOLUTIONS - L.L.C - S.P.C

The dropdown spelling should be retained exactly as configured, even where the source contains a spelling inconsistency.

## Category

* CAT A
* CAT B
* CAT C
* CAT F1 - TC1 TOP UP

## Relation

* Principal
* Spouse
* Child
* Others
* Ex-Spouse
* Parent

## Gender

* Male
* Female

## Marital Status

* Married
* Divorced
* Widowed
* Single

## Salary Band

* Salary less than AED 4,000 per month
* Salary between AED 4,001 and AED 12,000 per month
* Salary greater than AED 12,000 per month
* No salary

## Member Type

* UAE National
* GCC National
* Diplomat
* Expat whose residence was issued in Dubai
* Expat whose residence was issued outside Dubai
* New Born

## Commission

* No
* Yes

## Visa Issuance Emirate/Status

* Abu Dhabi
* Ajman
* Diplomat
* Dubai
* Dubai National
* Fujairah
* GCC National
* Ras Al Khaimah
* Sharjah
* UAE National
* Umm Al Quwain

---

# 6. Medical Declaration Codes

| Medical Condition                                         | NAS Code |
| --------------------------------------------------------- | -------- |
| Allergy conditions – chronic                              | ALLER126 |
| Arthritis                                                 | ARTH138  |
| Asthma                                                    | AST137   |
| Autoimmune Disorders                                      | AUTO125  |
| Cancer                                                    | CAN101   |
| Chronic Infectious Diseases                               | INFEC127 |
| Chronic Obstructive Pulmonary Disease                     | COPD141  |
| Chronic Circulatory System Conditions                     | CIR102   |
| Congenital and Genetic Conditions                         | COGEN103 |
| Dermatological Conditions                                 | DER104   |
| Diabetes                                                  | DIAB134  |
| Diseases of Blood and Blood-Forming Organs                | BLOOD128 |
| Encounters Related to Past Surgery                        | PSH122   |
| Endocrine Disorders                                       | END105   |
| Chronic Eye and Ear Conditions                            | EYEAR129 |
| Gastrointestinal Diseases                                 | GAST106  |
| Genitourinary Conditions                                  | GEN107   |
| Heart Disease                                             | HRT139   |
| Hyperlipidaemia                                           | LIPID136 |
| Hypertension                                              | HTN135   |
| Injury and Poisoning                                      | INJ130   |
| Kidney Diseases                                           | KIDN142  |
| Chronic Liver Diseases                                    | LIVR143  |
| Lymphatic System Conditions                               | PREG131  |
| Musculoskeletal Conditions                                | MUSK109  |
| Neurological Conditions                                   | NEU110   |
| Nutritional Conditions                                    | NUT111   |
| Pregnancy                                                 | MAT108   |
| Psychological Conditions                                  | PSY123   |
| Reproductive Health Disorders                             | REPR132  |
| Respiratory Diseases                                      | RESP124  |
| Sexually Transmitted Infections and Associated Conditions | STD133   |
| Stroke                                                    | STRK140  |
| Thyroid Disorders                                         | THYR144  |

The template currently maps **Lymphatic System Conditions** to `PREG131`. Since the code appears unusual in relation to the condition description, it should be confirmed with NAS before being amended or used extensively. The code should not be independently changed without confirmation.

---

# 7. Important Issues Identified in the Current Workbook

### 7.1 Department and Grade dropdowns are empty

The Department and Grade columns are linked to hidden lookup sheets, but both lookup sheets are currently blank. Therefore, these dropdowns will not provide selectable values until the relevant department and grade lists are added.

### 7.2 Non-UAE Sub-Nationality dropdown appears incorrectly linked

The workbook contains the value **“Other”** for non-UAE nationalities, but the named dropdown range points to the following blank cell instead. As a result, members with a nationality other than United Arab Emirates may receive a blank Sub-Nationality dropdown.

### 7.3 Work Region dropdown does not begin on the first member row

The Work Region validation starts from **Row 3**, while member data begins from **Row 2**. Therefore, the first member row may not receive a Work Region dropdown.

### 7.4 Several important fields are hidden

The following upload fields are hidden in the main sheet:

* Middle Name
* Arabic First Name
* Arabic Middle Name
* Arabic Last Name
* Department
* Grade
* Principal Card No.
* Sub-Nationality

They remain part of the upload structure even though they are not immediately visible.

### 7.5 Visa Type has no dropdown

The explanation sheet provides six Visa Type values, but the main upload column does not contain a dropdown. Users should therefore enter the expected wording consistently.

### 7.6 Certain fields are not properly defined

The workbook does not provide a complete operational explanation for:

* Commission
* Regulator No
* Department
* Grade
* Sub-Nationality
* Passport Expiry Date
* Visa Expiry Date
* Difference between PEC Declaration and Waived PEC Declaration

These fields should be confirmed with NAS before they are incorporated into an automated processing workflow.

---

# 8. Recommended Operational Interpretation

This template should be treated as a combination of:

1. **Member enrolment data:** Names, DOB, gender, nationality and contact details.
2. **Policy allocation data:** Contract, category and effective date.
3. **Family-linkage data:** Relation, principal card number and family number.
4. **Regulatory data:** Emirates ID, UID, passport, visa, salary and Dubai-related location information.
5. **Document index:** Photo and medical declaration filenames matching the ZIP attachments.
6. **Underwriting data:** COC availability, medical declaration codes and waived-condition codes.

For an employee addition, the row should contain the employee’s full policy, identity, employment and regulatory information.

For a dependant addition, the row should additionally identify the correct principal employee through the Principal Card Number and/or Family Number and use the appropriate relationship value.

For a family being uploaded together before card numbers have been issued, all members should be grouped consistently using the same Contract Name, Category and Family Number.

For a modification request, the relevant member should be identified through the Staff ID, Emirates ID, passport number, card number or another available unique identifier, and the updated field should be entered accurately.
