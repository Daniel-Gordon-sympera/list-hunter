# Feature: Parent Office and Counter
This feature add to the final CSV file two new columns and fill in the 'firm_website_url' column if it is empty.

## Introduction
Currently, the final CSV file contains a column for 'firm_website_url' which may be empty for some entries. In some of this cases, the 'firm_website_url' can be filled in by looking at the parent office of the law firm. This feature will add two new columns to the final CSV file: 
- 'parent_office'- The 'parent_office' column will contain the name of the parent office
- 'counter' column will indicate how many records belong to each parent office.

### Example
For the uuid '002d928f-b2e5-4fef-9659-f7be49f467bf', the 'firm_website_url' is empty. 
However, by looking at the html we can see that the 'the-law-office-of-sofia-balile-esq' is the parent office.

'''
<a class="profile-profile-header d-none d-xl-block paragraph-large-xl mb-0 mb-xl-3" href="https://profiles.superlawyers.com/new-york/brooklyn/lawfirm/the-law-office-of-sofia-balile-esq/bc2cf36e-fc72-410c-a2ed-c3e50dd09278.html" title="Super Lawyers Profile page of Firm The Law Office of Sofia Balile, Esq.">The Law Office of Sofia Balile, Esq.</a>
'''

Next, we can look for other records that belong to the same parent office and fill in the 'firm_website_url' column for those records as well. The 'counter' column will indicate how many records belong to the parent office 'the-law-office-of-sofia-balile-esq', and find out how many records belong to each parent office (1 in this case).

## Expected Outcome
After implementing this feature, the final CSV file will have two new columns and the 'firm_website_url' column will be filled in for records that belong to the same parent office. The 'counter' column will indicate how many records belong to each parent office (calculate after grouping), allowing for better analysis and insights into the data. This will enhance the overall quality and completeness of the data in the final CSV file, making it more useful for further analysis and decision-making.