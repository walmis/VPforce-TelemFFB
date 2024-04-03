Here are the full Python programming guidelines for best practices:

1. **Avoid Global Variables Using the "global" Keyword**:
   - Global variables can lead to unpredictable behavior and make code harder to debug and maintain. Avoid using them whenever possible.
   - Instead of relying on global variables, consider passing necessary variables as arguments to functions or using object-oriented design to encapsulate data.

2. **If Global Variables are Required, Use the "globals" Module**:
   - In cases where global variables are unavoidable, utilize the `globals` module.
   - Import the `globals` module as `G` for clarity and easy access.
   - Access global variables using the `G.variable_name` syntax to clearly indicate their global nature and avoid namespace collisions.

```python
import globals as G

# Example usage
G.my_var = 10
print(G.my_var)
```

3. **Larger Classes Should Live in Their Own Files**:
   - To maintain code organization and readability, larger classes should reside in separate files.
   - Each file should contain a single class definition or a tightly related group of classes.
   - Use meaningful file and class names to convey the purpose and functionality of the code.
   - Consider organizing related classes into modules or packages for better modularization and reuse.

Example directory structure:

```
project/
│
├── main.py
├── classes/
│   ├── __init__.py
│   ├── large_class.py
│   └── another_large_class.py
```

4. **Follow PEP 8 Style Guide**:
   - Adhere to the guidelines outlined in PEP 8 for consistent code style.
   - Use descriptive variable and function names to enhance readability.
   - Follow appropriate naming conventions, such as using lowercase with underscores for variable names (`snake_case`) and using CamelCase for class names.
   - Maintain consistent indentation and whitespace usage throughout the codebase.

5. **Document Your Code**:
   - Provide clear and concise documentation for classes, functions, and modules using docstrings.
   - Describe the purpose, parameters, return values, and any exceptions raised by functions and methods.
   - Follow the reStructuredText format for docstrings to ensure compatibility with tools like Sphinx for generating documentation.

```python
class MyClass:
    """A brief description of MyClass.

    Longer description if necessary.

    Attributes:
        attr1 (int): Description of attr1.
        attr2 (str): Description of attr2.
    """

    def __init__(self, attr1, attr2):
        """Initialize MyClass with given attributes.

        Args:
            attr1 (int): Description of attr1.
            attr2 (str): Description of attr2.
        """
        self.attr1 = attr1
        self.attr2 = attr2

    def my_method(self):
        """Brief description of my_method.

        Longer description if necessary.
        """
        pass
```

6. **Do Not Use Global Variables as Default Keyword Arguments**:
   - Avoid using global variables as default values for keyword arguments in function definitions.
   - Default arguments are evaluated at function definition time, and using global variables can lead to unexpected behavior or unintended side effects.
   - If default values are needed, prefer using immutable objects like `None` or define them within the function body to ensure predictable behavior.

Following these guidelines will lead to more readable, maintainable, and robust Python code.